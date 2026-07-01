"""Tests for the FastAPI web layer (``app.main``).

The service layer is exercised for real (kit save, campaign driver, index) but
B2 is the same ``FakeBackend`` used elsewhere and the Genblaze run is faked at
the ``build_image_pipeline`` seam (as in ``test_campaign``) so nothing hits the
network or bills a provider. Settings and the storage backend are injected via
``app.dependency_overrides`` and the provider pick is monkeypatched, so no real
credentials or SDK clients are needed. All tests are ``unit``.
"""

import types

import pytest
from fastapi.testclient import TestClient

from app import campaign as campaign_mod
from app import main as main_mod
from app import pipeline
from app.config import Settings
from app.main import create_app, get_backend, get_settings
from app.pipeline import PipelineError, ProviderChoice

USER = "curator"
PASSWORD = "s3cr3t-pass"
AUTH = (USER, PASSWORD)


def _settings(*, with_auth: bool = True) -> Settings:
    return Settings(
        b2_key_id="k",
        b2_app_key="a",
        b2_bucket="brandforge-media",
        b2_region="us-east-005",
        gmicloud_api_key=None,
        openai_api_key="sk-test",
        anthropic_api_key=None,
        public_base_url=None,
        basic_auth_user=USER if with_auth else None,
        basic_auth_pass=PASSWORD if with_auth else None,
    )


class FakeBackend:
    """Minimal stand-in for S3StorageBackend (matches tests/test_storage.py)."""

    def __init__(self):
        self.put_calls = []
        self.store = {}

    def put(self, key, data, *, content_type=None, **_):
        self.put_calls.append((key, data, content_type))
        self.store[key] = data
        return key

    def get(self, key, **_):
        return self.store[key]

    def exists(self, key):
        return key in self.store

    def get_url(self, key, *, policy=None, **_):
        return f"https://cdn.example/{key}?policy={policy}"


def _fake_pipe_factory(n: int):
    """A ``build_image_pipeline`` replacement whose run yields ``n`` media across
    ``n`` steps under one manifest (mirrors tests/test_campaign.py)."""
    steps = [
        types.SimpleNamespace(
            model="gpt-image-1",
            prompt=f"variant {i}",
            assets=[
                types.SimpleNamespace(
                    asset_id=f"a{i}",
                    url=f"https://b/brandforge/run1/a{i}.png",
                    sha256=f"sha{i}",
                )
            ],
        )
        for i in range(n)
    ]
    fake_result = types.SimpleNamespace(
        run=types.SimpleNamespace(steps=steps, run_id="run1"),
        manifest=types.SimpleNamespace(manifest_uri="brandforge/run1/manifest.json"),
        failed_steps=lambda: [],
        error_summary=lambda: None,
    )

    class FakePipe:
        def run(self, *, sink=None, timeout=None, raise_on_failure=None):
            return fake_result

    return lambda *a, **k: FakePipe()


def _client(settings: Settings, backend: FakeBackend) -> TestClient:
    """Build a TestClient whose settings + backend are injected (no real B2)."""
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_backend] = lambda: backend
    return TestClient(app)


def _brand(id: str = "acme") -> dict:
    return {"id": id, "name": "Acme", "tone_words": ["bold"], "style_prompt": "flat"}


def _campaign(brand_id: str = "acme", *, cid: str = "c1", n: int = 3) -> dict:
    return {"id": cid, "brand_kit_id": brand_id, "theme": "spring launch", "num_variants": n}


# --- health ---------------------------------------------------------------


@pytest.mark.unit
def test_healthz_is_open_and_reports_providers():
    client = _client(_settings(), FakeBackend())
    res = client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["providers"] == {"openai": True, "gmicloud": False}


# --- auth -----------------------------------------------------------------


@pytest.mark.unit
def test_protected_route_requires_credentials():
    client = _client(_settings(), FakeBackend())
    res = client.get("/assets")
    assert res.status_code == 401
    assert res.headers.get("WWW-Authenticate") == "Basic"


@pytest.mark.unit
def test_protected_route_rejects_wrong_credentials():
    client = _client(_settings(), FakeBackend())
    res = client.get("/assets", auth=("curator", "wrong"))
    assert res.status_code == 401


@pytest.mark.unit
def test_protected_route_fails_closed_when_auth_unconfigured():
    client = _client(_settings(with_auth=False), FakeBackend())
    res = client.get("/assets", auth=AUTH)
    assert res.status_code == 503


@pytest.mark.unit
def test_correct_credentials_pass():
    client = _client(_settings(), FakeBackend())
    res = client.get("/assets", auth=AUTH)
    assert res.status_code == 200
    assert res.json() == []


# --- brand kits -----------------------------------------------------------


@pytest.mark.unit
def test_post_brandkit_saves_and_returns_key_and_url():
    backend = FakeBackend()
    client = _client(_settings(), backend)
    res = client.post("/brandkits", json=_brand(), auth=AUTH)
    assert res.status_code == 201
    body = res.json()
    assert body["brand_kit_key"] == "brandkits/acme/v1.json"
    assert body["url"].startswith("https://cdn.example/brandkits/acme/v1.json")
    assert any(key == "brandkits/acme/v1.json" for key, _d, _c in backend.put_calls)


@pytest.mark.unit
def test_post_brandkit_rejects_invalid_id():
    client = _client(_settings(), FakeBackend())
    res = client.post("/brandkits", json={"id": "has space", "name": "X"}, auth=AUTH)
    assert res.status_code == 422  # pydantic pattern validation


# --- campaigns ------------------------------------------------------------


@pytest.mark.unit
def test_post_campaign_generates_and_indexes(monkeypatch):
    monkeypatch.setattr(pipeline, "build_image_pipeline", _fake_pipe_factory(3))
    monkeypatch.setattr(
        campaign_mod,
        "pick_image_provider",
        lambda *a, **k: ProviderChoice(provider=object(), model="gpt-image-1", name="openai:gpt-image-1"),
    )
    backend = FakeBackend()
    client = _client(_settings(), backend)

    res = client.post(
        "/campaigns", json={"brand": _brand(), "campaign": _campaign()}, auth=AUTH
    )
    assert res.status_code == 200
    body = res.json()
    assert body["manifest_uri"] == "brandforge/run1/manifest.json"
    assert len(body["assets"]) == 3
    # The set is now queryable through the API.
    listed = client.get("/assets", params={"campaign_id": "c1"}, auth=AUTH)
    assert len(listed.json()) == 3


@pytest.mark.unit
def test_post_campaign_rejects_brand_mismatch():
    client = _client(_settings(), FakeBackend())
    res = client.post(
        "/campaigns",
        json={"brand": _brand("acme"), "campaign": _campaign("other")},
        auth=AUTH,
    )
    assert res.status_code == 400
    assert "does not match" in res.json()["detail"]


@pytest.mark.unit
def test_campaign_response_hides_prompt_and_provider(monkeypatch):
    monkeypatch.setattr(pipeline, "build_image_pipeline", _fake_pipe_factory(1))
    monkeypatch.setattr(
        campaign_mod,
        "pick_image_provider",
        lambda *a, **k: ProviderChoice(provider=object(), model="gpt-image-1", name="openai:gpt-image-1"),
    )
    client = _client(_settings(), FakeBackend())
    res = client.post(
        "/campaigns", json={"brand": _brand(), "campaign": _campaign(n=1)}, auth=AUTH
    )
    asset = res.json()["assets"][0]
    assert "prompt" not in asset
    assert "provider" not in asset
    assert asset["model"] == "gpt-image-1"


# --- assets query + gallery ----------------------------------------------


@pytest.mark.unit
def test_assets_filter_by_campaign(monkeypatch):
    monkeypatch.setattr(pipeline, "build_image_pipeline", _fake_pipe_factory(2))
    monkeypatch.setattr(
        campaign_mod,
        "pick_image_provider",
        lambda *a, **k: ProviderChoice(provider=object(), model="gpt-image-1", name="openai:gpt-image-1"),
    )
    backend = FakeBackend()
    client = _client(_settings(), backend)
    client.post("/campaigns", json={"brand": _brand(), "campaign": _campaign(cid="c1", n=2)}, auth=AUTH)

    hit = client.get("/assets", params={"campaign_id": "c1"}, auth=AUTH)
    miss = client.get("/assets", params={"campaign_id": "nope"}, auth=AUTH)
    assert len(hit.json()) == 2
    assert miss.json() == []
    # URLs are re-signed from the durable key (FakeBackend.get_url shape).
    assert hit.json()[0]["url"].startswith("https://cdn.example/")


@pytest.mark.unit
def test_gallery_renders_html_with_assets(monkeypatch):
    monkeypatch.setattr(pipeline, "build_image_pipeline", _fake_pipe_factory(1))
    monkeypatch.setattr(
        campaign_mod,
        "pick_image_provider",
        lambda *a, **k: ProviderChoice(provider=object(), model="gpt-image-1", name="openai:gpt-image-1"),
    )
    backend = FakeBackend()
    client = _client(_settings(), backend)
    client.post("/campaigns", json={"brand": _brand(), "campaign": _campaign(cid="c1", n=1)}, auth=AUTH)

    res = client.get("/", auth=AUTH)
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Gallery" in res.text
    assert "a0" in res.text  # the generated asset id appears in a card


@pytest.mark.unit
def test_gallery_requires_auth():
    client = _client(_settings(), FakeBackend())
    res = client.get("/")
    assert res.status_code == 401


# --- POST-route auth gating (billable / state-changing) -------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/brandkits", {"id": "acme", "name": "Acme"}),
        ("post", "/campaigns", {"brand": {"id": "a", "name": "A"}, "campaign": {"id": "c", "brand_kit_id": "a", "theme": "t"}}),
    ],
)
def test_state_changing_routes_require_auth(method, path, body):
    client = _client(_settings(), FakeBackend())
    res = client.request(method, path, json=body)
    assert res.status_code == 401
    assert res.headers.get("WWW-Authenticate") == "Basic"


@pytest.mark.unit
def test_post_campaign_fails_closed_when_auth_unconfigured():
    client = _client(_settings(with_auth=False), FakeBackend())
    res = client.post(
        "/campaigns", json={"brand": _brand(), "campaign": _campaign()}, auth=AUTH
    )
    assert res.status_code == 503


# --- error mapping --------------------------------------------------------


@pytest.mark.unit
def test_pipeline_error_maps_to_502(monkeypatch):
    def _boom(*_a, **_k):
        raise PipelineError("provider exploded [run_id=x manifest=y]")

    monkeypatch.setattr(campaign_mod, "pick_image_provider", _boom)
    client = _client(_settings(), FakeBackend())
    res = client.post(
        "/campaigns", json={"brand": _brand(), "campaign": _campaign(n=1)}, auth=AUTH
    )
    assert res.status_code == 502
    # The bounded message must not leak the run locators from the exception.
    assert "run_id" not in res.text


# --- DI wiring (real get_backend, not overridden) -------------------------


@pytest.mark.unit
def test_get_backend_builds_once_and_caches(monkeypatch):
    backend = FakeBackend()
    calls = {"n": 0}

    def _fake_make_backend(_settings):
        calls["n"] += 1
        return backend

    monkeypatch.setattr(main_mod, "make_backend", _fake_make_backend)
    settings = _settings()
    app = create_app(settings=settings)
    # Only settings is overridden; get_backend runs for real and lazily builds.
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    client.get("/assets", auth=AUTH)
    client.get("/assets", auth=AUTH)
    assert calls["n"] == 1  # built once, then reused from app.state


# --- hardening ------------------------------------------------------------


@pytest.mark.unit
def test_security_headers_present():
    client = _client(_settings(), FakeBackend())
    res = client.get("/healthz")
    assert res.headers.get("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in res.headers.get("Content-Security-Policy", "")
    assert res.headers.get("X-Content-Type-Options") == "nosniff"


@pytest.mark.unit
def test_openapi_and_docs_are_disabled():
    client = _client(_settings(), FakeBackend())
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
