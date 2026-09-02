from fastapi import Depends, HTTPException, Request
from app.api.rate_limit import extract_client_key
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.services.observability import audit
from .sessions import load_session
from .permissions import role_has
import ipaddress

def ip_allowed(user, ip: str) -> bool:
    mode = settings.auth_ip_binding_mode
    if mode == "log_only":
        return True

    # registered_ip "0.0.0.0" is a wildcard — allow from any device.
    if user.registered_ip == "0.0.0.0":
        return True

    if mode == "strict":
        return ip == user.registered_ip
    elif mode == "cidr":
        if not user.allowed_cidr:
            return False
        try:
            return ipaddress.ip_address(ip) in ipaddress.ip_network(user.allowed_cidr)
        except ValueError:
            return False
    elif mode == "list":
        return bool(user.allowed_ips) and ip in user.allowed_ips

    return True

async def get_current_user(request: Request, db = Depends(get_db)):
    sid = request.cookies.get("rca_session")
    sess = await load_session(sid) if sid else None
    if not sess:
        raise HTTPException(401, "Not authenticated")

    user = db.get(User, sess["user_id"])
    if not user or not user.is_active:
        raise HTTPException(401, "Not authenticated")

    if settings.auth_ip_binding_mode != "log_only":
        client_ip = extract_client_key(request, trust_forwarded_for=settings.trust_forwarded_for)
        if not ip_allowed(user, client_ip):
            raise HTTPException(401, "Session ended — please log in again.")

    request.state.session_id = sid
    return user

def require(permission: str):
    async def _dep(request: Request, user = Depends(get_current_user)):
        if not role_has(user.role, permission):
            await audit.record(event_type="authz_denied", actor=user, request=request,
                               metadata={"permission": permission, "path": request.url.path})
            raise HTTPException(403, "You don't have access to this.")
        return user
    return _dep
