"""Completeness sweep merge/dedupe (recall fix, 2026-06-08).

After the first per-chunk grading pass, a second "what did you miss?" pass is run
and its findings are merged in. `merge_findings` is the pure, deterministic core:
combine first-pass + sweep findings while dropping exact duplicates (so a phrase
re-reported by the sweep isn't double-counted) and keeping genuinely distinct
findings (even when they quote the same phrase for different reasons).
"""
import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from app.schemas.compliance_schemas import (  # noqa: E402
    PrecedentCitation,
    RuleFinding,
    NovelFinding,
)
from app.services.agents.graph.nodes import merge_findings  # noqa: E402
from app.services.preprocessing_service import ContextEngineeringService  # noqa: E402


def _cit(idx, text, comment="this phrase is non-compliant"):
    return PrecedentCitation(
        precedent_index=idx, current_text=text, reviewer_comment=comment,
        action_type="rewrite", confidence=0.9,
    )


def _rule(idx, text, comment="rule violated by this phrase"):
    return RuleFinding(
        rule_index=idx, current_text=text, reviewer_comment=comment,
        action_type="rewrite", confidence=0.9,
    )


def _novel(text, comment="this is a novel issue not covered by precedent or rule", basis="IRDAI Ad Regs 2021"):
    return NovelFinding(
        current_text=text, reviewer_comment=comment, action_type="verify-source",
        regulatory_basis=basis, confidence=0.85,
    )


def test_sweep_adds_a_new_citation():
    cit, rule, novel, _pf = merge_findings(
        [_cit(0, "guaranteed 12% returns")], [], [],
        [_cit(1, "India's #1 ULIP")], [], [],
    )
    texts = sorted(c.current_text for c in cit)
    assert texts == ["India's #1 ULIP", "guaranteed 12% returns"]


def test_sweep_duplicate_citation_is_not_double_counted():
    cit, rule, novel, _pf = merge_findings(
        [_cit(0, "guaranteed 12% returns")], [], [],
        [_cit(0, "Guaranteed 12% Returns")], [], [],  # same idx, case/space variant
    )
    assert len(cit) == 1


def test_sweep_adds_new_novel_and_rule_findings():
    cit, rule, novel, _pf = merge_findings(
        [], [_rule(0, "fund managers consistently beat the market")], [_novel("UIN: pending")],
        [], [_rule(1, "85% of urban families are underinsured")], [_novel("retirees aged 65 and above")],
    )
    assert len(rule) == 2
    assert len(novel) == 2


def test_sweep_prompt_lists_already_found_and_asks_only_for_additional():
    svc = ContextEngineeringService(None)
    prompt = svc.create_completeness_sweep_prompt(
        "Guaranteed 12% returns. India's #1 ULIP.",
        precedents=[], rules=[],
        already_found=["Guaranteed 12% returns"],
    )
    assert "Guaranteed 12% returns" in prompt           # the already-found phrase is shown
    assert "ADDITIONAL" in prompt.upper()                # asks for new findings only
    assert "ALREADY" in prompt.upper()


def test_distinct_novel_findings_on_same_phrase_are_both_kept():
    # "up to 12%" can be flagged both as a guaranteed-return claim and as a
    # misleading projection — different reasons, must not collapse to one.
    cit, rule, novel, _pf = merge_findings(
        [], [], [_novel("up to 12%", comment="guaranteed return claim on a market-linked ULIP")],
        [], [], [_novel("up to 12%", comment="projection inconsistent with the 8% illustration")],
    )
    assert len(novel) == 2
