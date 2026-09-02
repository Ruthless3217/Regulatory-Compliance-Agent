"""IP binding (auth_ip_binding_mode: strict|cidr|list|log_only).

get_current_user used to call ip_allowed(sess, client_ip) with the raw
session dict. ip_allowed's dict branch only special-cased mode=='strict', so
'cidr' and 'list' silently fell through to an unconditional `return True` —
those two modes were a no-op on every authenticated request. get_current_user
now loads the User row first and calls ip_allowed(user, client_ip), which
routes every mode through the real check against the user's
registered_ip/allowed_cidr/allowed_ips.
"""
import asyncio
import uuid

import pytest

from app.auth.dependencies import get_current_user, ip_allowed
from app.config import settings
from app.models.user import User
from tests.support.fake_session import FakeSession


class _Request:
    """Minimal stand-in for fastapi.Request: cookies, headers, client.host."""
    def __init__(self, sid, client_ip):
        self.cookies = {"rca_session": sid} if sid else {}
        self.headers = {}
        self.client = type("Client", (), {"host": client_ip})()
        self.state = type("State", (), {})()


def _user(**kw):
    defaults = dict(id=uuid.uuid4(), username="u", role="user", is_active=True)
    defaults.update(kw)
    return User(**defaults)


# --- ip_allowed() itself, for each configured mode -------------------------

def test_cidr_mode_rejects_an_ip_outside_the_range(monkeypatch):
    monkeypatch.setattr(settings, "auth_ip_binding_mode", "cidr")
    user = _user(allowed_cidr="10.0.0.0/24")
    assert ip_allowed(user, "10.0.1.5") is False


def test_cidr_mode_accepts_an_ip_inside_the_range(monkeypatch):
    monkeypatch.setattr(settings, "auth_ip_binding_mode", "cidr")
    user = _user(allowed_cidr="10.0.0.0/24")
    assert ip_allowed(user, "10.0.0.5") is True


def test_list_mode_rejects_an_ip_not_on_the_list(monkeypatch):
    monkeypatch.setattr(settings, "auth_ip_binding_mode", "list")
    user = _user(allowed_ips=["1.2.3.4"])
    assert ip_allowed(user, "9.9.9.9") is False


def test_list_mode_accepts_an_ip_on_the_list(monkeypatch):
    monkeypatch.setattr(settings, "auth_ip_binding_mode", "list")
    user = _user(allowed_ips=["1.2.3.4"])
    assert ip_allowed(user, "1.2.3.4") is True


# --- wired through get_current_user, which used to bypass cidr/list --------

def test_get_current_user_rejects_a_non_matching_ip_in_cidr_mode(monkeypatch):
    db = FakeSession()
    user = _user(allowed_cidr="10.0.0.0/24")
    db.add(user)

    monkeypatch.setattr(settings, "auth_ip_binding_mode", "cidr")
    monkeypatch.setattr(
        "app.auth.dependencies.load_session",
        lambda sid: asyncio.sleep(0, result={"user_id": user.id}),
    )

    with pytest.raises(Exception) as exc:
        asyncio.run(get_current_user(_Request("sid", "203.0.113.9"), db=db))
    assert getattr(exc.value, "status_code", None) == 401


def test_get_current_user_accepts_a_matching_ip_in_cidr_mode(monkeypatch):
    db = FakeSession()
    user = _user(allowed_cidr="10.0.0.0/24")
    db.add(user)

    monkeypatch.setattr(settings, "auth_ip_binding_mode", "cidr")
    monkeypatch.setattr(
        "app.auth.dependencies.load_session",
        lambda sid: asyncio.sleep(0, result={"user_id": user.id}),
    )

    result = asyncio.run(get_current_user(_Request("sid", "10.0.0.7"), db=db))
    assert result is user
