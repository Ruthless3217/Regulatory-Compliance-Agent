import logging
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
from .sessions import load_session

logger = logging.getLogger(__name__)

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # We don't load user object here to avoid DB hit on every static/unauth request.
        # We just refresh the session sliding window if a session cookie is present.
        sid = request.cookies.get("rca_session")
        if sid:
            # load_session extends the TTL automatically
            try:
                await load_session(sid)
            except Exception as e:
                logger.warning(f"Error loading session in middleware: {e}")
        
        response = await call_next(request)
        return response
