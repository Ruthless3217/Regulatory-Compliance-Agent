"""CSRF Origin-check middleware (audit-trail 08 §4), flag-gated.

The primary CSRF gate is the ``SameSite=Strict`` session cookie. This adds a
second, cheap check: for state-changing methods, if an ``Origin`` header is
present and is NOT one of the allowed app origins, the request is rejected. It
requires no frontend change (browsers set Origin on cross-site sends). Disabled
by default (``settings.csrf_protect``) so a mis-configured CORS list can't lock
out the app; enable it in the locked-down prod environment.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if settings.csrf_protect and request.method in _UNSAFE_METHODS:
            origin = request.headers.get("origin")
            # Non-browser callers (curl, server-to-server) send no Origin; they
            # still need the session cookie, which SameSite=Strict protects.
            if origin and origin not in settings.api_cors_origins:
                return JSONResponse(
                    {"detail": "Cross-origin request blocked."}, status_code=403
                )
        return await call_next(request)
