"""Phase 4 (observability): best-effort append-only audit recorder.

Like the usage recorder, ``audit.record`` must NEVER raise on a DB failure and
must populate actor / ip / session from its inputs. The DB insert is stubbed and
the request is a lightweight fake (headers/client/state) — no Postgres, no HTTP.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app.services.observability import audit


class _FakeClient:
    host = "203.0.113.7"


def _fake_request(session_id="sess-abc"):
    return SimpleNamespace(
        headers={},                      # dict has .get(), like Starlette Headers
        client=_FakeClient(),
        state=SimpleNamespace(session_id=session_id),
    )


def _fake_actor(uid="user-9", role="admin"):
    return SimpleNamespace(id=uid, role=role)


def test_record_never_raises_on_db_error():
    with patch.object(audit, "_insert_audit_event", side_effect=RuntimeError("db down")):
        result = asyncio.run(
            audit.record("login", actor=_fake_actor(), request=_fake_request())
        )
    assert result is None


def test_record_populates_actor_ip_session():
    with patch.object(audit, "_insert_audit_event") as insert:
        asyncio.run(
            audit.record(
                "rule_updated",
                actor=_fake_actor(uid="user-9", role="admin"),
                request=_fake_request(session_id="S-123"),
                target_type="rule",
                target_id="rule-42",
                before={"text": "old"},
                after={"text": "new"},
                metadata={"version": 3},
            )
        )
    assert insert.call_count == 1
    kw = insert.call_args.kwargs
    assert kw["event_type"] == "rule_updated"
    assert kw["actor_user_id"] == "user-9"
    assert kw["actor_role"] == "admin"
    assert kw["actor_ip"] == "203.0.113.7"        # from request.client.host
    assert kw["session_id"] == "S-123"            # from request.state.session_id
    assert kw["target_type"] == "rule"
    assert kw["target_id"] == "rule-42"
    assert kw["before"] == {"text": "old"}
    assert kw["after"] == {"text": "new"}
    assert kw["metadata"] == {"version": 3}


def test_record_handles_missing_actor_and_request():
    # e.g. a system/ingestion event with no HTTP request and no actor.
    with patch.object(audit, "_insert_audit_event") as insert:
        asyncio.run(audit.record("login_failed", metadata={"reason": "bad_credentials"}))
    kw = insert.call_args.kwargs
    assert kw["actor_user_id"] is None
    assert kw["actor_role"] is None
    assert kw["actor_ip"] is None
    assert kw["session_id"] is None
    assert kw["metadata"] == {"reason": "bad_credentials"}


def test_actor_ip_uses_forwarded_for_when_trusted():
    from app.config import settings

    req = SimpleNamespace(
        headers={"x-forwarded-for": "198.51.100.5, 10.0.0.1"},
        client=_FakeClient(),
        state=SimpleNamespace(session_id="sess-xff"),
    )
    with patch.object(audit, "_insert_audit_event") as insert, patch.object(
        settings, "trust_forwarded_for", True
    ):
        asyncio.run(audit.record("login", actor=_fake_actor(), request=req))
    assert insert.call_args.kwargs["actor_ip"] == "198.51.100.5"
