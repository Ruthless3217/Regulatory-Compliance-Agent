"""/auth/* routes via FastAPI TestClient — DB stubbed, Redis session helpers
patched. No live services."""
import os
import sys
import types
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
from fastapi import FastAPI

from app.database import get_db
from app.auth.passwords import hash_password
import app.api.routes.auth as auth_routes
import app.auth.dependencies as deps

try:
    from fastapi.testclient import TestClient
    _HAVE_CLIENT = True
except Exception:  # httpx not installed
    _HAVE_CLIENT = False

pytestmark = pytest.mark.skipif(not _HAVE_CLIENT, reason="TestClient/httpx unavailable")

CURRENT_PW = "OldPassw0rd!!"


def _user(**kw):
    base = dict(
        id="11111111-1111-1111-1111-111111111111", username="rohit.sharma", role="user",
        is_active=True, must_change_password=False, registered_ip="testclient",
        allowed_cidr=None, allowed_ips=None, password_hash=hash_password(CURRENT_PW),
        last_login_at=None, password_updated_at=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


class _Query:
    def __init__(self, result):
        self._r = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._r

    def count(self):
        return 0


class _SessionRow:
    def __init__(self):
        self.logout_at = None
        self.login_at = datetime.now(timezone.utc)
        self.last_seen_at = None
        self.duration_seconds = None
        self.status = "active"


class _FakeDB:
    def __init__(self, user):
        self.user = user
        self.added = []

    def query(self, model):
        return _Query(self.user)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def get(self, model, pk):
        from app.models.user_session import UserSession
        if model is UserSession:
            return _SessionRow()
        return self.user


def _make_client(monkeypatch, user, session_ip="testclient"):
    async def _create_session(u, ip, ua):
        return "sid-abc"

    async def _noop(*a, **k):
        return None

    async def _load_session(sid):
        return {"user_id": str(user.id), "ip": session_ip} if user else None

    monkeypatch.setattr(auth_routes, "create_session", _create_session)
    monkeypatch.setattr(auth_routes, "revoke_session", _noop)
    monkeypatch.setattr(auth_routes, "revoke_all_for_user", _noop)
    monkeypatch.setattr(deps, "load_session", _load_session)
    # fresh lockout state per test
    auth_routes._failures.clear()

    fake_db = _FakeDB(user)

    def _override_db():
        yield fake_db

    app = FastAPI()
    app.include_router(auth_routes.router)
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def test_login_success_sets_cookie(monkeypatch):
    c = _make_client(monkeypatch, _user())
    r = c.post("/auth/login", json={"username": "rohit.sharma", "password": CURRENT_PW})
    assert r.status_code == 200, r.text
    assert r.json() == {"role": "user", "must_change_password": False}
    assert "rca_session" in r.cookies


def test_login_bad_password_401(monkeypatch):
    c = _make_client(monkeypatch, _user())
    r = c.post("/auth/login", json={"username": "rohit.sharma", "password": "wrong-password"})
    assert r.status_code == 401


def test_login_unknown_user_401(monkeypatch):
    c = _make_client(monkeypatch, None)
    r = c.post("/auth/login", json={"username": "ghost", "password": "whatever12345"})
    assert r.status_code == 401


def test_login_wrong_ip_403(monkeypatch):
    c = _make_client(monkeypatch, _user(registered_ip="10.0.0.5"))  # != testclient
    r = c.post("/auth/login", json={"username": "rohit.sharma", "password": CURRENT_PW})
    assert r.status_code == 403


def test_login_lockout_after_max_attempts(monkeypatch):
    monkeypatch.setattr(auth_routes.settings, "login_max_attempts", 3)
    c = _make_client(monkeypatch, _user())
    for _ in range(3):
        assert c.post("/auth/login", json={"username": "rohit.sharma", "password": "bad"}).status_code == 401
    # next attempt (even correct password) is locked out
    r = c.post("/auth/login", json={"username": "rohit.sharma", "password": CURRENT_PW})
    assert r.status_code == 429


def test_me_returns_identity(monkeypatch):
    c = _make_client(monkeypatch, _user())
    c.cookies.set("rca_session", "sid-abc")
    r = c.get("/auth/me")
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "rohit.sharma"
    assert r.json()["role"] == "user"


def test_change_password_rotates_and_clears_flag(monkeypatch):
    user = _user(must_change_password=True)
    c = _make_client(monkeypatch, user)
    c.cookies.set("rca_session", "sid-abc")
    r = c.post("/auth/change-password",
               json={"current_password": CURRENT_PW, "new_password": "BrandNewPass123"})
    assert r.status_code == 200, r.text
    assert user.must_change_password is False


def test_change_password_rejects_short(monkeypatch):
    c = _make_client(monkeypatch, _user())
    c.cookies.set("rca_session", "sid-abc")
    r = c.post("/auth/change-password",
               json={"current_password": CURRENT_PW, "new_password": "short"})
    assert r.status_code == 400


def test_logout_ok(monkeypatch):
    c = _make_client(monkeypatch, _user())
    c.cookies.set("rca_session", "sid-abc")
    r = c.post("/auth/logout")
    assert r.status_code == 200
