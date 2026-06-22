import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from app.services.agents.compliance import critic as critic_mod
from app.services.agents.compliance.critic import (
    critique_violations, _build_critic_prompt, CritiqueResult, CritiqueItem,
)


def _viol(grounding, sev="moderate", conf=0.9, text="bad claim", rule_id=None):
    return {
        "severity": sev, "confidence": conf, "current_text": text,
        "description": "desc", "rule_id": rule_id,
        "regulator_quote": "reg quote" if grounding == "rule" else None,
        "cited_comment_verbatim": "old reviewer note" if grounding == "precedent" else None,
        "cited_anchor_text": "anchor" if grounding == "precedent" else None,
        "violation_metadata": {"grounding": grounding},
    }


def test_prompt_includes_per_tier_evidence():
    viols = [_viol("precedent"), _viol("rule", rule_id=7), _viol("novel")]
    prompt = _build_critic_prompt("doc text", [{"id": 7, "rule_text": "no guarantees"}], viols)
    assert "precedent" in prompt.lower()
    assert "old reviewer note" in prompt          # precedent evidence
    assert "reg quote" in prompt or "rule_id=7" in prompt  # rule evidence
    assert "novel" in prompt.lower()              # novel tier labelled


def test_critic_uses_critic_llm_service_not_main():
    viols = [_viol("novel", conf=0.9)]
    fake = CritiqueResult(critiques=[CritiqueItem(index=0, keep=True, confidence=0.6, reason="ok")])
    with patch.object(critic_mod.critic_llm_service, "generate_structured_response",
                      new=AsyncMock(return_value=fake)) as mock_critic:
        out = asyncio.run(critique_violations("doc", [], viols, precedents=[]))
    mock_critic.assert_awaited_once()
    assert out[0]["confidence"] == pytest.approx(0.6)  # downgraded to min(0.9, 0.6)


def test_critic_drops_low_confidence_noncritical():
    viols = [_viol("novel", conf=0.9)]
    fake = CritiqueResult(critiques=[CritiqueItem(index=0, keep=False, confidence=0.2, reason="hallucinated")])
    with patch.object(critic_mod.critic_llm_service, "generate_structured_response",
                      new=AsyncMock(return_value=fake)):
        out = asyncio.run(critique_violations("doc", [], viols, precedents=[]))
    assert out == []


def test_critic_never_drops_critical():
    viols = [_viol("precedent", sev="critical", conf=0.9)]
    fake = CritiqueResult(critiques=[CritiqueItem(index=0, keep=False, confidence=0.1, reason="x")])
    with patch.object(critic_mod.critic_llm_service, "generate_structured_response",
                      new=AsyncMock(return_value=fake)):
        out = asyncio.run(critique_violations("doc", [], viols, precedents=[{"id": 1}]))
    assert len(out) == 1                          # critical survives
    assert out[0]["severity"] == "critical"
    assert out[0]["confidence"] == pytest.approx(0.9)  # confidence preserved, not gutted


def test_critic_critical_reject_confidence_unchanged():
    """critical + keep=False + very low critic_conf → survives with primary confidence intact."""
    viols = [_viol("rule", sev="critical", conf=0.9, rule_id=5)]
    fake = CritiqueResult(critiques=[CritiqueItem(index=0, keep=False, confidence=0.1, reason="disagree")])
    with patch.object(critic_mod.critic_llm_service, "generate_structured_response",
                      new=AsyncMock(return_value=fake)):
        out = asyncio.run(critique_violations("doc", [{"id": 5}], viols, precedents=[]))
    assert len(out) == 1
    assert out[0]["confidence"] == pytest.approx(0.9)  # must NOT be 0.1*0.5=0.05


def test_critic_fail_open_on_error():
    viols = [_viol("rule", rule_id=1, conf=0.8)]
    with patch.object(critic_mod.critic_llm_service, "generate_structured_response",
                      new=AsyncMock(side_effect=RuntimeError("boom"))):
        out = asyncio.run(critique_violations("doc", [{"id": 1}], viols, precedents=[]))
    assert out == viols                            # unchanged passthrough
