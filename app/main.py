"""FastAPI application for BrandForge — the thin web layer over the services.

The routers here own no business logic: they validate input, call the existing
service layer (``app.storage`` / ``app.campaign`` / ``app.index``), and shape a
response. Persistence, generation, and indexing all live below in those modules.

Design notes:
* ``create_app()`` builds the app and resolves one ``Settings`` + one storage
  ``backend`` at startup, shared via ``app.state`` and injected through
  dependencies (``get_settings`` / ``get_backend``) — tests override those.
* Service calls are **synchronous, blocking** I/O (B2, OpenAI), so every route
  handler is a plain ``def``; Starlette runs it in a threadpool instead of
  blocking the event loop.
* Every route except ``GET /healthz`` requires HTTP Basic auth (``app.security``)
  so presigned URLs and prompts are never served unauthenticated.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from genblaze_s3 import S3StorageBackend
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.campaign import run_campaign
from app.config import Settings, load_settings
from app.index import query_assets
from app.models import BrandKit
from app.pipeline import PipelineError
from app.schemas import AssetOut, BrandKitOut, CampaignOut, CampaignRequest
from app.security import make_auth_dependency
from app.storage import brand_kit_key, make_backend, save_brand_kit

# Rate limits. The public free-tier deploy has no upstream WAF, so a
# leaked/guessed credential could otherwise loop the billable generation route
# unbounded; these bound the blast radius. The key is the request peer address
# (``get_remote_address``). We intentionally do NOT trust the client-supplied
# X-Forwarded-For (no uvicorn ``--forwarded-allow-ips``), because an attacker
# could rotate that header to get a fresh bucket per request and bypass the
# limit; behind Render's edge every request therefore shares the proxy peer,
# making these effectively an UNSPOOFABLE global (service-wide) cap. Read limits
# are set high enough to leave headroom for concurrent legitimate viewers.
# /healthz is intentionally unlimited so the health probe is never throttled.
# The Limiter is built per-app in create_app() so each app instance (notably
# each test) gets isolated counters.
_LIMIT_CAMPAIGNS = "20/hour"   # billable (OpenAI gpt-image-1) — hard global ceiling
_LIMIT_BRANDKITS = "60/hour"
_LIMIT_READS = "240/minute"

# Locked-down response headers applied to every response (see create_app).
# Basic-auth credentials are cached per-origin by browsers, so a state-changing
# POST is frameable for clickjacking unless we forbid embedding; CSP is
# defence-in-depth over Jinja2's autoescaping.
_SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: https:; "
        "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
    ),
}

logger = logging.getLogger("brandforge")

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _ROOT / "templates"
_STATIC_DIR = _ROOT / "static"


def get_settings(request: Request) -> Settings:
    """Return the app's resolved settings (overridden in tests)."""
    return request.app.state.settings


def get_backend(request: Request) -> S3StorageBackend:
    """Return the app's shared storage backend, building it once on first use.

    Construction is lazy because ``make_backend`` runs a bucket preflight (a
    network round trip); deferring it keeps app creation (and tests that
    override this dependency) free of I/O, while production still reuses a single
    backend across requests rather than rebuilding per handler.
    """
    if request.app.state.backend is None:
        request.app.state.backend = make_backend(request.app.state.settings)
    return request.app.state.backend


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app.

    ``settings`` may be injected (tests); otherwise it is loaded from the
    environment, failing fast on missing B2 credentials. A single storage
    backend is created up front and reused for every request rather than being
    rebuilt per handler.
    """
    settings = settings or load_settings()

    # Docs/OpenAPI are disabled: they are not covered by the auth dependency and
    # would otherwise disclose the whole API surface to anonymous callers.
    app = FastAPI(
        title="BrandForge",
        version="0.6.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.backend = None  # built lazily on first request (see get_backend)
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _on_rate_limited)

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    auth = make_auth_dependency(get_settings)

    @app.middleware("http")
    async def _apply_security_headers(request: Request, call_next) -> Response:
        try:
            response = await call_next(request)
        except Exception:
            # An unhandled downstream error (e.g. a B2 StorageError from the lazy
            # backend build on cold start) would otherwise escape past this
            # middleware to Starlette's ServerErrorMiddleware and return a bare,
            # headerless text/plain 500. Convert it to the API's JSON error shape
            # and still attach the locked-down headers. Full detail is logged
            # server-side; the client message stays bounded (no secrets/locators).
            logger.exception("unhandled request error")
            response = JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Internal error."},
            )
        response.headers.update(_SECURITY_HEADERS)
        return response

    _register_exception_handlers(app)
    _register_routes(app, templates, auth, limiter)
    return app


def _on_rate_limited(_request: Request, _exc: RateLimitExceeded) -> JSONResponse:
    """Return the API's bounded JSON shape for a throttled request (429)."""
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Rate limit exceeded. Please slow down and try again later."},
    )


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(PipelineError)
    def _on_pipeline_error(_request: Request, exc: PipelineError) -> JSONResponse:
        # Generation or indexing failed downstream; the message carries B2
        # locators but no secrets. Log full detail, return a bounded message.
        logger.warning("pipeline error: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": "Generation failed. See server logs for the run locators."},
        )


def _register_routes(
    app: FastAPI, templates: Jinja2Templates, auth: Callable[..., None], limiter: Limiter
) -> None:
    @app.get("/healthz")
    def healthz(settings: Settings = Depends(get_settings)) -> dict:
        """Liveness probe (unauthenticated). Deliberately does not touch B2 so
        Render's health check stays fast and cold starts aren't billed a round
        trip."""
        return {
            "status": "ok",
            "providers": {
                "openai": settings.has_openai,
                "gmicloud": settings.has_gmicloud,
            },
        }

    @app.post("/brandkits", response_model=BrandKitOut, status_code=status.HTTP_201_CREATED)
    @limiter.limit(_LIMIT_BRANDKITS)
    def create_brandkit(
        request: Request,
        brand: BrandKit,
        _: None = Depends(auth),
        settings: Settings = Depends(get_settings),
        backend: S3StorageBackend = Depends(get_backend),
    ) -> BrandKitOut:
        """Persist a Brand Kit revision to B2 and return its delivery URL."""
        url = save_brand_kit(settings, brand, backend=backend)
        return BrandKitOut(brand_kit_key=brand_kit_key(brand), url=url)

    @app.post("/campaigns", response_model=CampaignOut)
    @limiter.limit(_LIMIT_CAMPAIGNS)
    def create_campaign(
        request: Request,
        body: CampaignRequest,
        _: None = Depends(auth),
        settings: Settings = Depends(get_settings),
        backend: S3StorageBackend = Depends(get_backend),
    ) -> CampaignOut:
        """Generate a campaign's variant set (BILLABLE) and index it.

        The brand/campaign ownership invariant is checked here so a mismatch is
        a clean 400, leaving ``PipelineError`` to mean a genuine downstream
        (generation/index) failure -> 502.
        """
        if body.campaign.brand_kit_id != body.brand.id:
            return _bad_request(
                f"campaign.brand_kit_id {body.campaign.brand_kit_id!r} does not match "
                f"brand.id {body.brand.id!r}"
            )
        result = run_campaign(settings, body.brand, body.campaign, backend=backend)
        return CampaignOut(
            brand_kit_url=result.brand_kit_url,
            manifest_uri=result.manifest.manifest_uri,
            assets=[AssetOut.from_asset(a) for a in result.assets],
        )

    @app.get("/assets", response_model=list[AssetOut])
    @limiter.limit(_LIMIT_READS)
    def list_assets(
        request: Request,
        _: None = Depends(auth),
        settings: Settings = Depends(get_settings),
        backend: S3StorageBackend = Depends(get_backend),
        brand_kit_id: str | None = None,
        campaign_id: str | None = None,
        modality: str | None = None,
    ) -> list[AssetOut]:
        """Query the Parquet catalog with fresh (re-signed) delivery URLs."""
        assets = query_assets(
            settings,
            brand_kit_id=brand_kit_id,
            campaign_id=campaign_id,
            modality=modality,
            backend=backend,
        )
        return [AssetOut.from_asset(a) for a in assets]

    @app.get("/")
    @limiter.limit(_LIMIT_READS)
    def gallery(
        request: Request,
        _: None = Depends(auth),
        settings: Settings = Depends(get_settings),
        backend: S3StorageBackend = Depends(get_backend),
        brand_kit_id: str | None = None,
        campaign_id: str | None = None,
    ) -> Response:
        """Server-render the gallery. Passing ``campaign_id`` replays a past
        campaign's set with freshly signed URLs."""
        assets = query_assets(
            settings,
            brand_kit_id=brand_kit_id,
            campaign_id=campaign_id,
            backend=backend,
        )
        return templates.TemplateResponse(
            request,
            "gallery.html",
            {
                "assets": [AssetOut.from_asset(a) for a in assets],
                "filter_campaign_id": campaign_id or "",
                "filter_brand_kit_id": brand_kit_id or "",
            },
        )


def _bad_request(detail: str) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": detail})


# Run with the app factory so importing this module has no side effects (no
# settings load / backend build at import time — tests import create_app freely):
#     uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
