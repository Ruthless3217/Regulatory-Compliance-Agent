"""Redis-backed server sessions — round trip, fail-closed, revocation, idle expiry.

Uses a faithful in-memory fake Redis (bytes values, like decode_responses=False)
patched in place of get_redis. No real Redis. asyncio.run avoids a
pytest-asyncio dependency.
"""
import os
import sys
import asyncio
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import app.auth.sessions as sessions_mod


class FakeRedis:
    def __init__(self):
        self.store = {}      # key(bytes) -> value(bytes)
        self.ttl = {}

    async def set(self, key, value, ex=None):
        k = key.encode() if isinstance(key, str) else key
        v = value.encode() if isinstance(value, str) else value
        self.store[k] = v
        if ex is not None:
            self.ttl[k] = ex

    async def get(self, key):
        k = key.encode() if isinstance(key, str) else key
        return self.store.get(k)

    async def delete(self, key):
        k = key.encode() if isinstance(key, str) else key
        self.store.pop(k, None)
        self.ttl.pop(k, None)

    async def expire(self, key, ttl):
        k = key.encode() if isinstance(key, str) else key
        if k in self.store:
            self.ttl[k] = ttl

    async def scan_iter(self, match=None):
        # naive glob: only supports trailing '*'
        prefix = (match or "").rstrip("*").encode()
        for k in list(self.store.keys()):
            if k.startswith(prefix):
                yield k


def _fake_user(role="user", must_change=False):
    return types.SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111", role=role,
        must_change_password=must_change,
    )


def _patch_redis(monkeypatch, redis):
    async def _get_redis():
        return redis
    monkeypatch.setattr(sessions_mod, "get_redis", _get_redis)


def test_create_then_load_round_trip(monkeypatch):
    r = FakeRedis()
    _patch_redis(monkeypatch, r)

    async def go():
        sid = await sessions_mod.create_session(_fake_user(role="admin"), "10.0.0.9", "UA/1")
        assert isinstance(sid, str) and len(sid) >= 20
        loaded = await sessions_mod.load_session(sid)
        assert loaded is not None
        assert loaded["user_id"] == "11111111-1111-1111-1111-111111111111"
        assert loaded["role"] == "admin"
        assert loaded["ip"] == "10.0.0.9"
    asyncio.run(go())


def test_create_fails_closed_when_redis_down(monkeypatch):
    _patch_redis(monkeypatch, None)

    async def go():
        try:
            await sessions_mod.create_session(_fake_user(), "10.0.0.9", "UA/1")
        except RuntimeError:
            return True
        return False
    assert asyncio.run(go()) is True


def test_load_returns_none_when_redis_down(monkeypatch):
    _patch_redis(monkeypatch, None)
    assert asyncio.run(sessions_mod.load_session("whatever")) is None


def test_revoke_session(monkeypatch):
    r = FakeRedis()
    _patch_redis(monkeypatch, r)

    async def go():
        sid = await sessions_mod.create_session(_fake_user(), "10.0.0.9", "UA/1")
        await sessions_mod.revoke_session(sid)
        return await sessions_mod.load_session(sid)
    assert asyncio.run(go()) is None


def test_revoke_all_for_user_only_targets_that_user(monkeypatch):
    r = FakeRedis()
    _patch_redis(monkeypatch, r)

    async def go():
        u1 = _fake_user()
        u2 = types.SimpleNamespace(id="22222222-2222-2222-2222-222222222222", role="user", must_change_password=False)
        s1 = await sessions_mod.create_session(u1, "10.0.0.1", "UA")
        s2 = await sessions_mod.create_session(u2, "10.0.0.2", "UA")
        await sessions_mod.revoke_all_for_user(str(u1.id))
        return (await sessions_mod.load_session(s1)), (await sessions_mod.load_session(s2))
    gone, kept = asyncio.run(go())
    assert gone is None
    assert kept is not None


def test_idle_timeout_expires_session(monkeypatch):
    r = FakeRedis()
    _patch_redis(monkeypatch, r)
    monkeypatch.setattr(sessions_mod.settings, "session_idle_ttl_seconds", 1)

    async def go():
        sid = await sessions_mod.create_session(_fake_user(), "10.0.0.9", "UA/1")
        # backdate last_seen beyond the idle window
        import json
        key = f"session:{sid}".encode()
        payload = json.loads(r.store[key])
        payload["last_seen"] = time.time() - 60
        r.store[key] = json.dumps(payload).encode()
        return await sessions_mod.load_session(sid)
    assert asyncio.run(go()) is None
