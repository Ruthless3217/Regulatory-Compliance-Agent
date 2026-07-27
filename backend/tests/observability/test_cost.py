"""Phase 4 (observability): token -> money cost model.

Pure math against ``settings.llm_prices`` (USD per 1,000 tokens). No DB, no
network — the price map is monkeypatched to deterministic rates.
"""
import pytest

from app.config import settings
from app.services.observability import cost


@pytest.fixture
def priced(monkeypatch):
    """Deterministic price map: one configured model + a _default fallback."""
    monkeypatch.setattr(
        settings,
        "llm_prices",
        {
            "gpt-test": {"input": 1.5, "output": 3.0},
            "_default": {"input": 0.1, "output": 0.2},
        },
        raising=False,
    )


def test_known_model_math(priced):
    in_cost, out_cost, total = cost.compute_cost("gpt-test", 2000, 1000)
    # (2000/1000)*1.5 = 3.0 ; (1000/1000)*3.0 = 3.0
    assert in_cost == pytest.approx(3.0)
    assert out_cost == pytest.approx(3.0)
    assert total == pytest.approx(6.0)
    assert cost.price_source_for("gpt-test") == "configured"


def test_unknown_model_falls_back_to_default(priced):
    in_cost, out_cost, total = cost.compute_cost("no-such-model", 1000, 1000)
    # falls back to _default {"input":0.1,"output":0.2}
    assert in_cost == pytest.approx(0.1)
    assert out_cost == pytest.approx(0.2)
    assert total == pytest.approx(0.3)
    assert cost.price_source_for("no-such-model") == "default"


def test_zero_tokens_are_free(priced):
    assert cost.compute_cost("gpt-test", 0, 0) == (0.0, 0.0, 0.0)


def test_default_price_source_math_uses_default_rates(priced):
    # unknown model must still be *costed* (not silently zero) via _default.
    _, _, total = cost.compute_cost("mystery", 1000, 0)
    assert total == pytest.approx(0.1)
