"""health_check() must not hit the provider on every call.

GET /health calls this per request and the frontend polls it continuously. In
production that was ~2 live Azure round-trips every 10s (~17k/day), drawn from
the SAME per-minute token quota the analysis pipeline needs — health polling was
competing with grading for throughput.

These pin the cache: one upstream call per model per TTL, verdict preserved,
profiles isolated.
"""
import asyncio

import pytest

import app.services.llm_service as mod


class _FakeModels:
    def __init__(self, counter, fail=False):
        self._counter = counter
        self._fail = fail

    async def list(self):
        self._counter["n"] += 1
        if self._fail:
            raise RuntimeError("upstream down")
        return ["ok"]


class _FakeClient:
    def __init__(self, counter, fail=False):
        self.models = _FakeModels(counter, fail)


class _Svc:
    """Minimal stand-in exposing exactly what health_check touches."""
    health_check = mod.LLMService.health_check

    def __init__(self, model, counter, fail=False):
        self.model = model
        self.client = _FakeClient(counter, fail)


@pytest.fixture(autouse=True)
def _clear_cache():
    mod._HEALTH_CACHE.clear()
    yield
    mod._HEALTH_CACHE.clear()


def test_repeated_calls_hit_upstream_once():
    counter = {"n": 0}
    svc = _Svc("gpt-5.4-nano", counter)
    for _ in range(25):
        assert asyncio.run(svc.health_check()) is True
    assert counter["n"] == 1, "health polling still round-trips to the provider"


def test_cache_expires_after_ttl(monkeypatch):
    counter = {"n": 0}
    svc = _Svc("gpt-5.4-nano", counter)

    now = [1000.0]
    monkeypatch.setattr(mod, "_monotonic", lambda: now[0])

    asyncio.run(svc.health_check())
    assert counter["n"] == 1

    now[0] += mod._HEALTH_TTL_SECONDS - 1      # still inside the window
    asyncio.run(svc.health_check())
    assert counter["n"] == 1

    now[0] += 2                                 # now past it
    asyncio.run(svc.health_check())
    assert counter["n"] == 2


def test_profiles_do_not_share_a_verdict():
    # analysis / chat / critic can be different deployments; one being healthy
    # must never vouch for another.
    a, b = {"n": 0}, {"n": 0}
    asyncio.run(_Svc("gpt-5.4", a).health_check())
    asyncio.run(_Svc("gpt-5.4-nano", b).health_check())
    assert a["n"] == 1 and b["n"] == 1
    assert set(mod._HEALTH_CACHE) == {"gpt-5.4", "gpt-5.4-nano"}


def test_failure_is_cached_too_on_non_azure(monkeypatch):
    # Caching only successes would let a hard-down provider be re-probed on
    # every request — the exact stampede this fix exists to prevent.
    monkeypatch.setattr(mod.settings, "llm_provider", "openai")
    counter = {"n": 0}
    svc = _Svc("groq-llama", counter, fail=True)
    assert asyncio.run(svc.health_check()) is False
    assert asyncio.run(svc.health_check()) is False
    assert counter["n"] == 1


def test_azure_fallback_actually_runs(monkeypatch):
    """Regression: this branch read settings.llm_is_azure, which does not exist
    on Settings, so it raised AttributeError instead of reporting available."""
    monkeypatch.setattr(mod.settings, "llm_provider", "azure")
    counter = {"n": 0}
    svc = _Svc("gpt-5.4-nano", counter, fail=True)
    assert asyncio.run(svc.health_check()) is True
