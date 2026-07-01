"""HTTP Basic authentication for the web layer.

A single shared credential (``BRANDFORGE_USER`` / ``BRANDFORGE_PASS``) guards
every route that would otherwise expose asset presigned URLs, prompts, or
provider details. The design is deliberately fail-closed: if the credential is
not configured, protected routes return 503 rather than serving unauthenticated
— so a public deploy is never accidentally open.

Only ``GET /healthz`` is left unauthenticated (Render's health probe).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import Settings

_basic = HTTPBasic(auto_error=False)

# Sent on 401 so browsers show their native login dialog for the gallery.
_WWW_AUTH = {"WWW-Authenticate": "Basic"}


def _credentials_match(credentials: HTTPBasicCredentials, settings: Settings) -> bool:
    """Constant-time compare of both username and password.

    Both halves are compared with ``compare_digest`` (and always both, never
    short-circuiting) so timing does not leak which half was wrong.
    """
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        (settings.basic_auth_user or "").encode("utf-8"),
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        (settings.basic_auth_pass or "").encode("utf-8"),
    )
    return user_ok and pass_ok


def require_auth(settings: Settings, credentials: HTTPBasicCredentials | None) -> None:
    """Authorize a request, or raise the appropriate HTTP error.

    * 503 when auth is not configured (fail closed — never serve open).
    * 401 (with ``WWW-Authenticate: Basic``) when credentials are absent or wrong.
    """
    if not settings.has_auth:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured (set BRANDFORGE_USER/PASS).",
        )
    if credentials is None or not _credentials_match(credentials, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
            headers=_WWW_AUTH,
        )


def make_auth_dependency(get_settings: Callable[[Request], Settings]) -> Callable[..., None]:
    """Build the ``require_auth`` FastAPI dependency bound to a settings getter.

    ``get_settings`` is the app's settings provider (overridable in tests via
    ``app.dependency_overrides``), so auth reads the same Settings the routes do.
    """

    def _dependency(
        credentials: HTTPBasicCredentials | None = Depends(_basic),
        settings: Settings = Depends(get_settings),
    ) -> None:
        require_auth(settings, credentials)

    return _dependency
