"""Auth context middleware (audit-trail 05 §2.5).

For any request carrying a valid session cookie, binds the request-scoped
``usage_context`` (user_id + session_id) so every LLM call made while handling
the request is attributed to the acting user — even before/without a route
dependency running. It does NOT enforce auth (that is each route's
``require(...)`` dependency); public routes (login, health) pass through.

The context is reset in a ``finally`` so it never leaks across requests/tasks.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.sessions import load_session
from app.services.observability.usage_context import (
    UsageContext, bind_usage_context, reset_usage_context,
)


class AuthContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        token = None
        try:
            sid = request.cookies.get("rca_session")
            if sid:
                try:
                    sess = await load_session(sid)
                except Exception:
                    sess = None
                if sess:
                    request.state.session_id = sid
                    # Expose the user id early so the paid-endpoint rate limiter
                    # can key on the person (more precise than IP behind a proxy).
                    request.state.user_id = sess.get("user_id")
                    token = bind_usage_context(
                        UsageContext(user_id=sess.get("user_id"), session_id=sid)
                    )
            return await call_next(request)
        finally:
            if token is not None:
                reset_usage_context(token)
