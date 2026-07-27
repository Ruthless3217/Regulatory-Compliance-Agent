"""IP binding modes + get_current_user + require(permission) (audit-trail 01/05)."""
import os
import sys
import asyncio
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import app.auth.dependencies as deps
from fastapi import HTTPException


def _user(**kw):
    base = dict(
        id="11111111-1111-1111-1111-111111111111", role="user", is_active=True,
        registered_ip="10.20.14.37", allowed_cidr=None, allowed_ips=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


class FakeRequest:
    def __init__(self, cookies=None, ip="10.20.14.37", xff=None):
        self.cookies = cookies or {}
        self.headers = {"x-forwarded-for": xff} if xff else {}
        self.client = types.SimpleNamespace(host=ip)
        self.state = types.SimpleNamespace()
        self.url = types.SimpleNamespace(path="/rules")


class StubDB:
    def __init__(self, user):
        self._user = user

    def get(self, model, pk):
        return self._user


# ---- ip_allowed_for_user -------------------------------------------------

def test_strict_matches_registered_ip():
    assert deps.ip_allowed_for_user(_user(), "10.20.14.37", mode="strict") is True
    assert deps.ip_allowed_for_user(_user(), "10.20.14.99", mode="strict") is False


def test_strict_denies_when_no_registered_ip():
    assert deps.ip_allowed_for_user(_user(registered_ip=None), "10.20.14.37", mode="strict") is False


def test_cidr_mode():
    u = _user(allowed_cidr="10.20.14.0/24")
    assert deps.ip_allowed_for_user(u, "10.20.14.200", mode="cidr") is True
    assert deps.ip_allowed_for_user(u, "10.20.15.1", mode="cidr") is False
    assert deps.ip_allowed_for_user(_user(allowed_cidr=None), "10.20.14.1", mode="cidr") is False


def test_list_mode():
    u = _user(allowed_ips=["10.0.0.1", "10.0.0.2"])
    assert deps.ip_allowed_for_user(u, "10.0.0.2", mode="list") is True
    assert deps.ip_allowed_for_user(u, "10.0.0.9", mode="list") is False


def test_log_only_always_true():
    assert deps.ip_allowed_for_user(_user(registered_ip=None), "1.2.3.4", mode="log_only") is True


# ---- get_current_user ----------------------------------------------------

def _patch_session(monkeypatch, sess):
    async def _load(sid):
        return sess
    monkeypatch.setattr(deps, "load_session", _load)


def test_get_current_user_happy(monkeypatch):
    monkeypatch.setattr(deps.settings, "auth_ip_binding_mode", "strict")
    _patch_session(monkeypatch, {"user_id": "11111111-1111-1111-1111-111111111111", "ip": "10.20.14.37"})
    u = _user()
    req = FakeRequest(cookies={"rca_session": "sid123"}, ip="10.20.14.37")
    got = asyncio.run(deps.get_current_user(req, StubDB(u)))
    assert got is u
    assert req.state.session_id == "sid123"


def test_get_current_user_no_cookie(monkeypatch):
    _patch_session(monkeypatch, None)
    req = FakeRequest(cookies={})
    try:
        asyncio.run(deps.get_current_user(req, StubDB(_user())))
        assert False, "expected 401"
    except HTTPException as e:
        assert e.status_code == 401


def test_get_current_user_inactive(monkeypatch):
    _patch_session(monkeypatch, {"user_id": "11111111-1111-1111-1111-111111111111", "ip": "10.20.14.37"})
    req = FakeRequest(cookies={"rca_session": "sid"}, ip="10.20.14.37")
    try:
        asyncio.run(deps.get_current_user(req, StubDB(_user(is_active=False))))
        assert False
    except HTTPException as e:
        assert e.status_code == 401


def test_get_current_user_ip_mismatch_denied(monkeypatch):
    monkeypatch.setattr(deps.settings, "auth_ip_binding_mode", "strict")
    _patch_session(monkeypatch, {"user_id": "11111111-1111-1111-1111-111111111111", "ip": "10.20.14.37"})
    req = FakeRequest(cookies={"rca_session": "sid"}, ip="10.20.14.99")  # different machine
    try:
        asyncio.run(deps.get_current_user(req, StubDB(_user())))
        assert False
    except HTTPException as e:
        assert e.status_code == 401


# ---- require(permission) -------------------------------------------------

def test_require_allows_permitted_role():
    dep = deps.require("rules:read")
    got = asyncio.run(dep(FakeRequest(), user=_user(role="user")))
    assert got.role == "user"


def test_require_denies_forbidden_role():
    dep = deps.require("rules:write")
    try:
        asyncio.run(dep(FakeRequest(), user=_user(role="user")))
        assert False, "expected 403"
    except HTTPException as e:
        assert e.status_code == 403
