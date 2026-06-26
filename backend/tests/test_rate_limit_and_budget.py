"""Unit tests for the API cost/abuse guardrails.

Covers the *pure* decision logic only — the fixed-window counter, the
client-key extraction (incl. X-Forwarded-For), and the daily-budget threshold.
The async Redis wrappers are thin I/O and degrade gracefully; they are not
exercised here (no pytest-asyncio / live Redis in the suite).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.rate_limit import FixedWindowLimiter, extract_client_key
from app.services.llm_budget import is_over_budget


# --- FixedWindowLimiter (in-memory fallback) ---------------------------------

class _Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def test_limiter_allows_up_to_limit():
    clk = _Clock()
    lim = FixedWindowLimiter(limit=3, window_seconds=60, clock=clk)
    assert [lim.allow("ip") for _ in range(3)] == [True, True, True]


def test_limiter_blocks_beyond_limit():
    clk = _Clock()
    lim = FixedWindowLimiter(limit=2, window_seconds=60, clock=clk)
    lim.allow("ip"); lim.allow("ip")
    assert lim.allow("ip") is False


def test_limiter_resets_after_window():
    clk = _Clock()
    lim = FixedWindowLimiter(limit=1, window_seconds=60, clock=clk)
    assert lim.allow("ip") is True
    assert lim.allow("ip") is False
    clk.t += 61  # advance past the window
    assert lim.allow("ip") is True


def test_limiter_keys_are_independent():
    clk = _Clock()
    lim = FixedWindowLimiter(limit=1, window_seconds=60, clock=clk)
    assert lim.allow("a") is True
    assert lim.allow("b") is True  # different key, own bucket


# --- extract_client_key (X-Forwarded-For handling) ---------------------------

class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, host=None, xff=None):
        self.client = _FakeClient(host) if host else None
        self.headers = {}
        if xff is not None:
            self.headers["x-forwarded-for"] = xff


def test_client_key_uses_peer_ip_when_not_trusting_xff():
    req = _FakeRequest(host="10.0.0.5", xff="1.2.3.4")
    assert extract_client_key(req, trust_forwarded_for=False) == "10.0.0.5"


def test_client_key_uses_first_xff_hop_when_trusted():
    req = _FakeRequest(host="10.0.0.5", xff="1.2.3.4, 70.0.0.1")
    assert extract_client_key(req, trust_forwarded_for=True) == "1.2.3.4"


def test_client_key_trims_xff_whitespace():
    req = _FakeRequest(host="10.0.0.5", xff="  9.9.9.9 ,8.8.8.8")
    assert extract_client_key(req, trust_forwarded_for=True) == "9.9.9.9"


def test_client_key_falls_back_to_peer_when_xff_absent():
    req = _FakeRequest(host="10.0.0.5")
    assert extract_client_key(req, trust_forwarded_for=True) == "10.0.0.5"


def test_client_key_unknown_when_no_client():
    req = _FakeRequest(host=None)
    assert extract_client_key(req, trust_forwarded_for=False) == "unknown"


def test_client_key_ignores_empty_xff():
    req = _FakeRequest(host="10.0.0.5", xff="   ")
    assert extract_client_key(req, trust_forwarded_for=True) == "10.0.0.5"


# --- is_over_budget (daily token ceiling) ------------------------------------

def test_budget_disabled_when_zero():
    assert is_over_budget(spent=10_000_000, budget=0) is False


def test_budget_disabled_when_negative():
    assert is_over_budget(spent=10_000_000, budget=-1) is False


def test_under_budget_allowed():
    assert is_over_budget(spent=999, budget=1000) is False


def test_at_budget_is_over():
    # Once spend reaches the ceiling, the next call is refused (fail closed).
    assert is_over_budget(spent=1000, budget=1000) is True


def test_over_budget_refused():
    assert is_over_budget(spent=1001, budget=1000) is True
