"""Unit tests for chat input-size caps (prompt-bloat / cost guard)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.routes.chat import clamp_text, clamp_history


def test_clamp_text_under_limit_unchanged():
    assert clamp_text("hello", 100) == "hello"


def test_clamp_text_truncates_long():
    out = clamp_text("x" * 50, 10)
    assert len(out) <= 11  # 10 + ellipsis char
    assert out.startswith("xxxxxxxxxx")


def test_clamp_text_non_string():
    assert clamp_text(None, 10) == ""


def test_clamp_history_keeps_most_recent():
    hist = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    out = clamp_history(hist, max_messages=3, max_chars=100)
    assert len(out) == 3
    assert [m["content"] for m in out] == ["m7", "m8", "m9"]


def test_clamp_history_truncates_each_message():
    hist = [{"role": "user", "content": "y" * 500}]
    out = clamp_history(hist, max_messages=10, max_chars=20)
    assert len(out[0]["content"]) <= 21


def test_clamp_history_preserves_roles():
    hist = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    out = clamp_history(hist, max_messages=10, max_chars=100)
    assert [m["role"] for m in out] == ["user", "assistant"]


def test_clamp_history_empty():
    assert clamp_history([], max_messages=5, max_chars=100) == []


def test_clamp_history_drops_blank_content():
    hist = [{"role": "user", "content": ""}, {"role": "user", "content": "real"}]
    out = clamp_history(hist, max_messages=10, max_chars=100)
    assert out == [{"role": "user", "content": "real"}]
