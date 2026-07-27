"""LLMService._record_usage_event → usage_recorder.record (Phase 4 hook).

Confirms the token/cost capture hook fires with the right attribution regardless
of whether the provider returned a usage object. No live LLM/DB.
"""
import os
import sys
import asyncio
import types
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.llm_service import llm_service  # module singleton (profile="main")


def test_measured_usage_is_recorded():
    usage = types.SimpleNamespace(prompt_tokens=120, completion_tokens=30, total_tokens=150)
    with patch("app.services.observability.usage_recorder.record", new=AsyncMock()) as rec:
        asyncio.run(llm_service._record_usage_event(usage, [{"role": "user", "content": "hi"}]))
        rec.assert_awaited_once()
        kw = rec.await_args.kwargs
        assert kw["prompt_tokens"] == 120
        assert kw["completion_tokens"] == 30
        assert kw["token_source"] == "measured"
        assert kw["profile"] == "main"
        assert kw["model"] == llm_service.model
        assert kw["provider"] == llm_service.provider


def test_missing_usage_falls_back_to_estimate():
    with patch("app.services.observability.usage_recorder.record", new=AsyncMock()) as rec:
        asyncio.run(llm_service._record_usage_event(None, [{"role": "user", "content": "hello world"}]))
        kw = rec.await_args.kwargs
        assert kw["token_source"] == "estimated"
        assert kw["completion_tokens"] == 0
        assert kw["prompt_tokens"] >= 0


def test_hook_never_raises_on_recorder_error():
    async def _boom(**kw):
        raise RuntimeError("db down")

    with patch("app.services.observability.usage_recorder.record", new=_boom):
        # must swallow — a metering failure must never break a grade
        asyncio.run(llm_service._record_usage_event(
            types.SimpleNamespace(prompt_tokens=1, completion_tokens=1), [{"role": "user", "content": "x"}]))
