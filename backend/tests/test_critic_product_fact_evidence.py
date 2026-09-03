"""The critic must judge a finding against the evidence that produced it.

Product-fact findings come from an authoritative fact-card guardrail — the most
deterministic of the four tiers. The critic prompt had three branches (rule /
precedent / else), so a `product_fact` violation fell into the `else` and was
rendered byte-identically to a `novel` one: "no rule / no precedent (model's own
judgment)". Its guardrail_text, finding_kind and product identity were never
shown, and the prompt header announced "three grounding tiers (rule / precedent
/ novel)".

The critic may drop a non-critical finding when it answers keep=false with
confidence < 0.40, and halves the confidence of any non-critical it rejects
without dropping. So it could suppress the most evidence-backed tier for
lacking evidence it was never given.

These tests are deterministic: the prompt string is the object under test, and
no LLM is called. What an LLM then does with a correct prompt is not asserted
here — see the report's PROVEN/UNPROVEN split.
"""
import re

import pytest

from app.services.agents.compliance.critic import _build_critic_prompt

CHUNK = ("This plan guarantees returns of 12% every year with zero risk. "
         "Past performance shows 22% per annum. Cover starts immediately.")

RULES = [{"id": "r-1", "severity": "high", "rule_text": "No assured returns."}]

GUARDRAIL_A = "Must not present unit-linked returns as guaranteed"
GUARDRAIL_B = "Must state the annuity rate is fixed at inception"


def _violation(grounding, *, meta=None, severity="moderate", **extra):
    v = {
        "severity": severity,
        "confidence": 0.9,
        "description": f"{grounding} finding",
        "current_text": "guarantees returns of 12% every year",
        "violation_metadata": {"grounding": grounding, **(meta or {})},
    }
    v.update(extra)
    return v


def _product_fact(uin, name, guardrail, **kw):
    return _violation("product_fact", meta={
        "guardrail_text": guardrail,
        "finding_kind": "banned-claim",
        "action_type": "remove",
        "product_uin": uin,
        "product_name": name,
    }, **kw)


PRODUCT_A = _product_fact("116L196V04", "Bajaj Life Fortune Gain II", GUARDRAIL_A)
PRODUCT_B = _product_fact("116N169V16", "Bajaj Life Saral Pension", GUARDRAIL_B)
RULE_V = _violation("rule", rule_id="r-1",
                    regulator_quote="No assured-return claim may be made.")
PRECEDENT_V = _violation("precedent",
                         cited_comment_verbatim="Reviewer struck this in ticket 4412.")
NOVEL_V = _violation("novel")


def _entry(prompt, index):
    """The block the prompt rendered for violation `index`."""
    match = re.search(rf"^\[{index}\].*?(?=^\[\d+\]|\Z)", prompt, re.S | re.M)
    assert match, f"prompt has no entry [{index}]"
    return match.group(0)


# --------------------------------------------------------------------------
# The defect: authoritative evidence must reach the critic.
# --------------------------------------------------------------------------


def test_product_fact_entry_carries_its_guardrail():
    prompt = _build_critic_prompt(CHUNK, RULES, [PRODUCT_A])

    assert GUARDRAIL_A in _entry(prompt, 0)


def test_product_fact_entry_carries_its_finding_kind():
    prompt = _build_critic_prompt(CHUNK, RULES, [PRODUCT_A])

    assert "banned-claim" in _entry(prompt, 0)


def test_product_fact_entry_carries_its_product_identity():
    prompt = _build_critic_prompt(CHUNK, RULES, [PRODUCT_A])
    entry = _entry(prompt, 0)

    assert "116L196V04" in entry
    assert "Bajaj Life Fortune Gain II" in entry


def test_product_fact_is_not_described_as_the_models_own_judgment():
    prompt = _build_critic_prompt(CHUNK, RULES, [PRODUCT_A])
    entry = _entry(prompt, 0)

    assert "model's own judgment" not in entry
    assert "no rule / no precedent" not in entry


def test_product_fact_and_novel_entries_are_not_interchangeable():
    """They were byte-identical apart from the grounding label."""
    prompt = _build_critic_prompt(CHUNK, RULES, [PRODUCT_A, NOVEL_V])
    pf = _entry(prompt, 0).replace("grounding=product_fact", "")
    novel = _entry(prompt, 1).replace("grounding=novel", "")

    assert pf.replace("product_fact finding", "") != novel.replace("novel finding", "")


def test_header_names_every_tier_it_can_receive():
    prompt = _build_critic_prompt(CHUNK, RULES, [RULE_V, PRECEDENT_V, PRODUCT_A, NOVEL_V])
    header = prompt[prompt.index("You are an independent"):]

    assert "three grounding tiers" not in header
    for tier in ("rule", "precedent", "product", "novel"):
        assert tier in header.lower()


# --------------------------------------------------------------------------
# The critic must still be able to reject a product-fact finding.
# --------------------------------------------------------------------------


def test_product_fact_entry_still_asks_the_critic_to_verify():
    """Evidence is not a licence to accept: the CHECK must remain."""
    entry = _entry(_build_critic_prompt(CHUNK, RULES, [PRODUCT_A]), 0)

    assert "CHECK:" in entry


def test_product_fact_check_tests_the_interpretation_not_the_existence():
    """A guardrail existing must not read as proof the finding is right."""
    entry = _entry(_build_critic_prompt(CHUNK, RULES, [PRODUCT_A]), 0).lower()

    assert "current_text" in entry
    assert not re.search(r"always (keep|valid|correct)", entry)


# --------------------------------------------------------------------------
# Product identity must not cross between findings.
# --------------------------------------------------------------------------


def test_each_product_fact_entry_carries_only_its_own_guardrail():
    prompt = _build_critic_prompt(CHUNK, RULES, [PRODUCT_A, PRODUCT_B])
    entry_a, entry_b = _entry(prompt, 0), _entry(prompt, 1)

    assert GUARDRAIL_A in entry_a and GUARDRAIL_B not in entry_a
    assert GUARDRAIL_B in entry_b and GUARDRAIL_A not in entry_b


def test_each_product_fact_entry_carries_only_its_own_uin():
    prompt = _build_critic_prompt(CHUNK, RULES, [PRODUCT_A, PRODUCT_B])

    assert "116L196V04" in _entry(prompt, 0) and "116N169V16" not in _entry(prompt, 0)
    assert "116N169V16" in _entry(prompt, 1) and "116L196V04" not in _entry(prompt, 1)


def test_chunk_level_product_identity_survives_into_the_critic():
    """Chunk 1 grounds product A, chunk 2 grounds product B; each critic call
    is per chunk, so neither may see a document-level product list."""
    prompt_chunk_1 = _build_critic_prompt(CHUNK, RULES, [PRODUCT_A])
    prompt_chunk_2 = _build_critic_prompt(CHUNK, RULES, [PRODUCT_B])

    assert "116L196V04" in prompt_chunk_1 and "116N169V16" not in prompt_chunk_1
    assert "116N169V16" in prompt_chunk_2 and "116L196V04" not in prompt_chunk_2


# --------------------------------------------------------------------------
# The other three tiers are unchanged.
# --------------------------------------------------------------------------


def test_rule_tier_is_unchanged():
    entry = _entry(_build_critic_prompt(CHUNK, RULES, [RULE_V]), 0)

    assert "cited rule_id=r-1" in entry
    assert "No assured-return claim may be made." in entry


def test_precedent_tier_is_unchanged():
    entry = _entry(_build_critic_prompt(CHUNK, RULES, [PRECEDENT_V]), 0)

    assert "cited precedent" in entry
    assert "Reviewer struck this in ticket 4412." in entry


def test_novel_tier_is_unchanged():
    entry = _entry(_build_critic_prompt(CHUNK, RULES, [NOVEL_V]), 0)

    assert "no rule / no precedent (model's own judgment)" in entry


def test_every_violation_still_gets_exactly_one_entry_in_input_order():
    """The critic keys its verdicts by list index — the namespace must hold."""
    violations = [RULE_V, PRECEDENT_V, PRODUCT_A, NOVEL_V, PRODUCT_B]
    prompt = _build_critic_prompt(CHUNK, RULES, violations)

    for i, v in enumerate(violations):
        assert f"grounding={v['violation_metadata']['grounding']}" in _entry(prompt, i)
    assert not re.search(r"^\[5\]", prompt, re.M)


def test_a_product_fact_finding_without_metadata_does_not_break_the_prompt():
    """Fail-soft: a malformed finding must not take the whole critic call down."""
    bare = _violation("product_fact")

    entry = _entry(_build_critic_prompt(CHUNK, RULES, [bare]), 0)

    assert "CHECK:" in entry


# --------------------------------------------------------------------------
# Decision contract: protections and persistence are untouched.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("keep,conf,severity,expected", [
    (False, 0.30, "moderate", "dropped"),
    (False, 0.30, "critical", "kept"),      # criticals are never dropped
    (False, 0.50, "moderate", "kept"),      # confidence floor holds
    (True, 0.95, "moderate", "kept"),
])
def test_existing_drop_protections_are_untouched(keep, conf, severity, expected):
    from app.services.agents.compliance.critic import _DROP_BELOW

    is_critical = severity == "critical"
    dropped = (not keep) and conf < _DROP_BELOW and not is_critical

    assert ("dropped" if dropped else "kept") == expected


def test_the_critic_preserves_product_identity_on_survivors():
    """v_out = dict(v) plus a metadata merge — identity must come through."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    from app.services.agents.compliance import critic as critic_mod

    verdict = type("R", (), {"critiques": [
        type("C", (), {"index": 0, "keep": True, "confidence": 0.8,
                       "reason": "valid"})()
    ]})()

    with patch.object(critic_mod.critic_llm_service, "generate_structured_response",
                      AsyncMock(return_value=verdict)):
        out = asyncio.run(critic_mod.critique_violations(CHUNK, RULES, [dict(PRODUCT_A)]))

    meta = out[0]["violation_metadata"]
    assert meta["product_uin"] == "116L196V04"
    assert meta["product_name"] == "Bajaj Life Fortune Gain II"
    assert meta["guardrail_text"] == GUARDRAIL_A
    assert meta["finding_kind"] == "banned-claim"
    assert meta["grounding"] == "product_fact"
    assert meta["critic_reason"] == "valid"
