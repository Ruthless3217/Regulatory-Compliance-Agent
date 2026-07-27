"""RBAC enforcement matrix — role × permission grid via ``require(permission)``.

Verifies the ENFORCEMENT MECHANISM (the FastAPI gate), not the real routers:
we build a tiny in-test app whose routes are guarded by ``Depends(require(perm))``
and override ``get_current_user`` to inject a role directly (bypassing the Redis
session + IP binding). For every role/permission pair the guarded route must
return 200 iff ``role_has(role, perm)`` and 403 otherwise. An unauthenticated
call (no override) must 401.

No DB / Redis / LLM: the current-user dependency is stubbed and the deny-path
audit hook is a best-effort import that is absent in this phase (swallowed).
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
from fastapi import Depends, FastAPI

from app.auth.dependencies import get_current_user, require
from app.auth.permissions import ROLE_PERMISSIONS, role_has

try:
    from fastapi.testclient import TestClient
    _HAVE_CLIENT = True
except Exception:  # httpx not installed
    _HAVE_CLIENT = False

pytestmark = pytest.mark.skipif(not _HAVE_CLIENT, reason="TestClient/httpx unavailable")

# Every permission granted to any role — the full catalogue under test.
ALL_PERMISSIONS = sorted(set().union(*ROLE_PERMISSIONS.values()))
ROLES = sorted(ROLE_PERMISSIONS.keys())

# Task-mandated representative slice spanning grader, rule-authority, and
# console-only permissions (must include at least these).
REPRESENTATIVE_PERMISSIONS = [
    "analysis:run",
    "chat:use",
    "rules:read",
    "rules:write",
    "rules:generate",
    "console:view",
    "usage:view",
    "users:manage",
    "dashboard:view",
    "comparison:use",
]


@pytest.fixture(autouse=True)
def _stub_audit(monkeypatch):
    """The 403 deny path best-effort-records an ``authz_denied`` event, which
    opens a real ``SessionLocal`` and commits. Stub it to an async no-op so the
    matrix (which triggers many denials) never touches the DB. Runtime patch
    only — the service source is left untouched."""
    import app.services.observability.audit as audit_mod

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(audit_mod, "record", _noop)


def _path(perm: str) -> str:
    return "/p/" + perm.replace(":", "_")


def _build_app() -> FastAPI:
    """One guarded GET route per permission; ``require(perm)`` is bound at
    add-time (Depends is constructed immediately, so no late-binding leak)."""
    app = FastAPI()
    for perm in ALL_PERMISSIONS:
        def _handler(_user=Depends(require(perm))):
            return {"ok": True}

        app.add_api_route(_path(perm), _handler, methods=["GET"], name=f"guard_{perm}")
    return app


def _client_for_role(role: str) -> TestClient:
    app = _build_app()

    async def _fake_current_user():
        return SimpleNamespace(
            role=role,
            id="00000000-0000-0000-0000-000000000000",
            is_active=True,
        )

    app.dependency_overrides[get_current_user] = _fake_current_user
    return TestClient(app)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("perm", REPRESENTATIVE_PERMISSIONS)
def test_representative_grid_allows_iff_granted(role, perm):
    client = _client_for_role(role)
    resp = client.get(_path(perm))
    if role_has(role, perm):
        assert resp.status_code == 200, f"{role} should be allowed {perm}: {resp.text}"
        assert resp.json() == {"ok": True}
    else:
        assert resp.status_code == 403, f"{role} should be denied {perm}: {resp.text}"


@pytest.mark.parametrize("role", ROLES)
def test_full_catalogue_grid_for_role(role):
    """Exhaustive sweep: every permission × this role matches ``role_has``."""
    client = _client_for_role(role)
    for perm in ALL_PERMISSIONS:
        resp = client.get(_path(perm))
        expected = 200 if role_has(role, perm) else 403
        assert resp.status_code == expected, (
            f"{role} × {perm}: expected {expected}, got {resp.status_code} ({resp.text})"
        )


def test_unauthenticated_call_is_401():
    """With no ``get_current_user`` override, the real dependency runs and 401s
    (no session cookie). Confirms the gate fails closed before authz."""
    app = _build_app()
    client = TestClient(app)
    resp = client.get(_path("chat:use"))
    assert resp.status_code == 401, resp.text


def test_representative_slice_covers_required_permissions():
    """Guard against drift: the mandated permissions must exist in the catalogue
    (so the grid above is actually exercising real grants/denies)."""
    for perm in REPRESENTATIVE_PERMISSIONS:
        assert perm in ALL_PERMISSIONS, f"{perm} missing from ROLE_PERMISSIONS union"
