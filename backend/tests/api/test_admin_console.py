"""/super_admin/* console API via FastAPI TestClient.

No live DB / Redis / LLM. The Postgres ORM (UUID/JSONB/Numeric) rules out
sqlite, so the DB session is a hand-rolled stub that returns canned rollup rows
from a per-test queue; ``get_current_user`` is overridden to inject a role and
``audit.record`` is patched to a spy (no Postgres write, and lets us assert
emitted events). Mirrors ``tests/auth/test_auth_routes.py``: skip if httpx is
unavailable.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
from fastapi import FastAPI

from app.database import get_db
import app.auth.dependencies as deps
import app.api.routes.admin_console as ac
from app.services.observability import audit

try:
    from fastapi.testclient import TestClient
    _HAVE_CLIENT = True
except Exception:  # httpx not installed
    _HAVE_CLIENT = False

pytestmark = pytest.mark.skipif(not _HAVE_CLIENT, reason="TestClient/httpx unavailable")


# --- DB stub ---------------------------------------------------------------

class _StubQuery:
    """Chainable query whose terminals pop from the session's result queue."""

    def __init__(self, session):
        self._s = session

    def filter(self, *a, **k):
        return self

    def filter_by(self, *a, **k):
        return self

    def join(self, *a, **k):
        return self

    def outerjoin(self, *a, **k):
        return self

    def group_by(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def having(self, *a, **k):
        return self

    def distinct(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def offset(self, *a, **k):
        return self

    def all(self):
        return self._s._pop("all")

    def first(self):
        return self._s._pop("first")

    def scalar(self):
        return self._s._pop("scalar")

    def count(self):
        return self._s._pop("count")

    def one_or_none(self):
        return self._s._pop("first")


class _StubSession:
    def __init__(self, results=None, get_result=None):
        # queue of return values for terminal query methods, in call order
        self._results = list(results or [])
        self._get_result = get_result
        self.added = []
        self.commits = 0

    def _pop(self, kind):
        if self._results:
            return self._results.pop(0)
        return [] if kind == "all" else (0 if kind == "count" else None)

    def query(self, *a, **k):
        return _StubQuery(self)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        pass

    def flush(self):
        pass

    def get(self, model, pk):
        return self._get_result


# --- helpers ---------------------------------------------------------------

def _actor(role, uid="11111111-1111-1111-1111-111111111111"):
    return SimpleNamespace(id=uid, role=role, username=f"{role}-user", is_active=True)


def _make_client(monkeypatch, role, results=None, get_result=None):
    """Build an app with only the console router, inject ``role`` and a stub DB.

    Returns (client, session, audit_calls)."""
    session = _StubSession(results=results, get_result=get_result)

    def _override_db():
        yield session

    async def _override_user():
        return _actor(role)

    audit_calls = []

    async def _spy(event_type, **kw):
        audit_calls.append((event_type, kw))

    monkeypatch.setattr(audit, "record", _spy)

    async def _noop_revoke(user_id):
        return None

    monkeypatch.setattr(ac, "revoke_all_for_user", _noop_revoke)

    app = FastAPI()
    app.include_router(ac.router)
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[deps.get_current_user] = _override_user
    return TestClient(app), session, audit_calls


# ===========================================================================
# RBAC gating
# ===========================================================================

def test_super_admin_can_list_users(monkeypatch):
    users = [
        SimpleNamespace(
            id="u1", username="rohit", role="user", registered_ip="10.0.0.1",
            is_active=True, last_login_at=None, must_change_password=False, created_at=None,
        )
    ]
    run_rows = [SimpleNamespace(uid="u1", runs=3)]
    cost_rows = [SimpleNamespace(uid="u1", cost=1.25)]
    c, _, _ = _make_client(monkeypatch, "super_admin", results=[users, run_rows, cost_rows])
    r = c.get("/super_admin/users")
    assert r.status_code == 200, r.text
    body = r.json()["users"][0]
    assert body["username"] == "rohit"
    assert body["runs"] == 3
    assert body["total_cost_usd"] == 1.25


def test_super_admin_gets_usage_summary(monkeypatch):
    rows = [
        SimpleNamespace(uid="u1", username="rohit", role="user",
                        input_tokens=1000, output_tokens=250, cost_usd=0.42, runs=2)
    ]
    c, _, _ = _make_client(monkeypatch, "super_admin", results=[rows])
    r = c.get("/super_admin/usage/summary?days=30")
    assert r.status_code == 200, r.text
    u = r.json()["users"][0]
    assert u["input_tokens"] == 1000
    assert u["output_tokens"] == 250
    assert u["total_cost_usd"] == 0.42
    assert u["runs"] == 2


def test_user_role_denied_usage_view(monkeypatch):
    c, _, _ = _make_client(monkeypatch, "user")
    assert c.get("/super_admin/usage/summary").status_code == 403


def test_user_role_denied_audit_view(monkeypatch):
    c, _, _ = _make_client(monkeypatch, "user")
    assert c.get("/super_admin/audit").status_code == 403


def test_admin_denied_usage_view(monkeypatch):
    # admin has users:manage but NOT usage:view.
    c, _, _ = _make_client(monkeypatch, "admin")
    assert c.get("/super_admin/usage/summary").status_code == 403


def test_admin_denied_audit_view(monkeypatch):
    c, _, _ = _make_client(monkeypatch, "admin")
    assert c.get("/super_admin/rules/audit").status_code == 403


# ===========================================================================
# User provisioning + Decision D3
# ===========================================================================

def test_admin_can_create_user_account(monkeypatch):
    # duplicate check -> None (no existing user)
    c, session, audit_calls = _make_client(monkeypatch, "admin", results=[None])
    r = c.post("/super_admin/users", json={
        "username": "new.grader", "password": "TempPass123456",
        "registered_ip": "10.0.0.9", "role": "user",
    })
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "user"
    # a User row was staged with must_change_password + is_active
    created = session.added[0]
    assert created.username == "new.grader"
    assert created.must_change_password is True
    assert created.is_active is True
    assert any(ev == "user_created" for ev, _ in audit_calls)


def test_admin_cannot_create_admin_account(monkeypatch):
    # D3: admin actor may create only role="user".
    c, session, _ = _make_client(monkeypatch, "admin", results=[None])
    r = c.post("/super_admin/users", json={
        "username": "power", "password": "TempPass123456", "role": "admin",
    })
    assert r.status_code == 403
    assert session.added == []  # nothing persisted


def test_super_admin_can_create_admin_account(monkeypatch):
    c, session, _ = _make_client(monkeypatch, "super_admin", results=[None])
    r = c.post("/super_admin/users", json={
        "username": "power", "password": "TempPass123456", "role": "admin",
    })
    assert r.status_code == 201, r.text
    assert session.added[0].role == "admin"


def test_create_user_duplicate_conflict(monkeypatch):
    existing = SimpleNamespace(id="u1", username="dupe")
    c, session, _ = _make_client(monkeypatch, "super_admin", results=[existing])
    r = c.post("/super_admin/users", json={
        "username": "dupe", "password": "TempPass123456", "role": "user",
    })
    assert r.status_code == 409
    assert session.added == []


def test_create_user_hashes_password_and_audits(monkeypatch):
    seen = {}

    def _fake_hash(raw):
        seen["raw"] = raw
        return "argon2$stub$digest"

    monkeypatch.setattr(ac, "hash_password", _fake_hash)
    c, session, audit_calls = _make_client(monkeypatch, "super_admin", results=[None])
    r = c.post("/super_admin/users", json={
        "username": "carol", "password": "SuperSecret999", "role": "user",
    })
    assert r.status_code == 201, r.text
    created = session.added[0]
    # hash_password was used; plaintext is NEVER stored
    assert seen["raw"] == "SuperSecret999"
    assert created.password_hash == "argon2$stub$digest"
    assert "SuperSecret999" not in str(created.password_hash)
    # user_created audit emitted, and the after-image never contains the password
    user_created = [kw for ev, kw in audit_calls if ev == "user_created"]
    assert len(user_created) == 1
    after = user_created[0].get("after") or {}
    assert after.get("username") == "carol"
    assert "password" not in after
    assert "SuperSecret999" not in str(after)


# ===========================================================================
# Rollup shape checks
# ===========================================================================

def test_usage_by_document_shape(monkeypatch):
    rows = [
        SimpleNamespace(sid="s1", title="Brochure A", graded_by="rohit",
                        input_tokens=500, output_tokens=120, cost_usd=0.3,
                        total_runs=2, last_run=None)
    ]
    c, _, _ = _make_client(monkeypatch, "super_admin", results=[rows])
    r = c.get("/super_admin/usage/by-document?days=30")
    assert r.status_code == 200, r.text
    d = r.json()["documents"][0]
    assert d["title"] == "Brochure A"
    assert d["total_runs"] == 2
    assert d["input_tokens"] == 500


def test_runs_list_shape(monkeypatch):
    rows = [
        SimpleNamespace(
            id="r1", submission_id="s1", triggered_by="u1", run_number=2, is_rerun=True,
            trigger_source="stream", status="failed", degraded_reason="llm_timeout",
            duration_ms=1234, prompt_tokens=10, completion_tokens=5, total_tokens=15,
            total_cost_usd=0.01, started_at=None, finished_at=None,
        )
    ]
    c, _, _ = _make_client(monkeypatch, "super_admin", results=[rows])
    r = c.get("/super_admin/runs?days=30")
    assert r.status_code == 200, r.text
    run = r.json()["runs"][0]
    assert run["run_number"] == 2
    assert run["is_rerun"] is True
    assert run["degraded_reason"] == "llm_timeout"
    assert run["total_cost_usd"] == 0.01


def test_sessions_shape(monkeypatch):
    sess_rows = [
        SimpleNamespace(
            id="sid-1", user_id="u1", username="rohit", ip="10.0.0.1",
            login_at=None, last_seen_at=None, logout_at=None,
            duration_seconds=3600, status="closed",
        )
    ]
    active_rows = [SimpleNamespace(username="rohit", sessions=1, active_seconds=3600)]
    c, _, _ = _make_client(monkeypatch, "super_admin", results=[sess_rows, active_rows])
    r = c.get("/super_admin/sessions?days=7")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sessions"][0]["duration_seconds"] == 3600
    assert body["per_user_active_time"][0]["active_seconds"] == 3600


def test_audit_feed_exposes_metadata(monkeypatch):
    rows = [
        SimpleNamespace(
            id="a1", event_type="login", actor_user_id="u1", actor_role="user",
            actor_ip="10.0.0.1", session_id="sid-1", target_type=None, target_id=None,
            before=None, after=None, event_metadata={"reason": "ok"}, created_at=None,
        )
    ]
    c, _, _ = _make_client(monkeypatch, "super_admin", results=[rows])
    r = c.get("/super_admin/audit?days=30")
    assert r.status_code == 200, r.text
    ev = r.json()["events"][0]
    assert ev["event_type"] == "login"
    assert ev["metadata"] == {"reason": "ok"}  # from event_metadata attribute


def test_rules_audit_returns_rule_events(monkeypatch):
    rows = [
        SimpleNamespace(
            id="a2", event_type="rule_updated", actor_user_id="u1", actor_role="admin",
            actor_ip="10.0.0.1", session_id="sid-1", target_type="rule", target_id="rule-42",
            before={"text": "old"}, after={"text": "new"}, event_metadata={"version": 3},
            created_at=None,
        )
    ]
    c, _, _ = _make_client(monkeypatch, "super_admin", results=[rows])
    r = c.get("/super_admin/rules/audit?days=30")
    assert r.status_code == 200, r.text
    ev = r.json()["events"][0]
    assert ev["event_type"] == "rule_updated"
    assert ev["before"] == {"text": "old"}
    assert ev["after"] == {"text": "new"}


def test_usage_csv_export_streams_and_audits(monkeypatch):
    rows = [
        SimpleNamespace(username="rohit", role="user",
                        input_tokens=1000, output_tokens=200, cost_usd=0.5, runs=2)
    ]
    c, _, audit_calls = _make_client(monkeypatch, "super_admin", results=[rows])
    r = c.get("/super_admin/export/usage.csv")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "username,role,input_tokens" in r.text
    assert "rohit" in r.text
    assert any(ev == "usage_exported" for ev, _ in audit_calls)


def test_force_logout_revokes_and_audits(monkeypatch):
    c, _, audit_calls = _make_client(monkeypatch, "super_admin")
    r = c.post("/super_admin/users/u1/force-logout")
    assert r.status_code == 200, r.text
    assert any(ev == "user_force_logout" for ev, _ in audit_calls)


def test_patch_user_disable_revokes_and_audits(monkeypatch):
    target = SimpleNamespace(
        id="u1", username="rohit", role="user", registered_ip="10.0.0.1",
        is_active=True, must_change_password=False, password_hash="x",
        password_updated_at=None,
    )
    c, _, audit_calls = _make_client(monkeypatch, "super_admin", get_result=target)
    r = c.patch("/super_admin/users/u1", json={"is_active": False})
    assert r.status_code == 200, r.text
    assert target.is_active is False
    assert any(ev == "user_disabled" for ev, _ in audit_calls)
