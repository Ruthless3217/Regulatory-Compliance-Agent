"""Cost model: tokens -> money (Decision D6).

Prices live in ``settings.llm_prices`` as **USD per 1,000 tokens**, per model:
``{model: {"input": x, "output": y}, ..., "_default": {...}}``. An unknown model
falls back to the ``_default`` rate and is flagged ``price_source="default"`` so
the console can warn that a model's price is unconfigured (avoids silently
under-reporting spend).
"""
from __future__ import annotations

from typing import Dict, Tuple

from app.config import settings

_ZERO_RATES: Dict[str, float] = {"input": 0.0, "output": 0.0}


def _rates_for(model: str) -> Tuple[Dict[str, float], str]:
    """Return ``(rates, price_source)`` for ``model``.

    ``price_source`` is ``"configured"`` when the model has an explicit entry,
    else ``"default"`` (fell back to the ``_default`` rate).
    """
    prices = getattr(settings, "llm_prices", None) or {}
    rates = prices.get(model)
    if rates is not None:
        return rates, "configured"
    return prices.get("_default", _ZERO_RATES), "default"


def price_source_for(model: str) -> str:
    """``"configured"`` if the model has explicit rates, else ``"default"``."""
    return _rates_for(model)[1]


def compute_cost(
    model: str, prompt_tokens: int, completion_tokens: int
) -> Tuple[float, float, float]:
    """Return ``(input_cost, output_cost, total_cost)`` in USD.

    Rates are per 1,000 tokens. Unknown model -> ``_default`` rate.
    """
    rates, _ = _rates_for(model)
    input_cost = ((prompt_tokens or 0) / 1000.0) * float(rates.get("input", 0.0))
    output_cost = ((completion_tokens or 0) / 1000.0) * float(rates.get("output", 0.0))
    return input_cost, output_cost, input_cost + output_cost
