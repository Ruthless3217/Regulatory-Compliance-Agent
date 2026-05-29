"""Golden integration test for reviewer-voice commentary (2026-05-28 design).

Locks the user-visible behaviour to the specific Smart Secure corpus examples
that motivated the work: reviewer-voice substance, action-type bucketing,
evidence_needed, no meta-bridges, and at least one regulatory-grounded novel
finding.

LLM-gated: this makes real model calls, so it is skipped unless RUN_LLM_GOLDEN=1
(Groq has a daily token cap). Retrieval is bypassed — precedents are hand-built
from the actual reviewer comments so the test is deterministic about WHICH
precedents are in play and only the LLM's adaptation is under test.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LLM_GOLDEN") != "1",
    reason="LLM-gated golden test; set RUN_LLM_GOLDEN=1 to enable",
)

FORBIDDEN = ("similar to a precedent", "the precedent flagged")

# Six synthetic chunks. Each carries the precedents retrieval would surface
# (empty list = novel-only mode) and what we expect the reviewer to produce.
CHUNKS = [
    {
        "key": "fund-switch",
        "text": "Smart Secure allows you to switch between different investment "
                "funds based on your financial goals and market outlook.",
        "precedents": [{
            "id": "00000000-0000-0000-0000-000000000001",
            "document_id": "t-fundswitch",
            "anchor_text": "switch between investment funds",
            "comment_text": (
                "Include clear information Switching between fund under Investor "
                "Selectable Portfolio Strategy or investment portfolio strategies "
                "is free of the Miscellaneous Charge.. portfolio strategies can be "
                "switched only during policy anniversary"
            ),
            "violation_category": "legal language",
            "severity": "moderate",
            "score": 0.9,
        }],
        "expect_action": "rewrite",
        "expect_substring": "Investor Selectable Portfolio Strategy",
    },
    {
        "key": "uw-sa",
        "text": "Get comprehensive life coverage up to ₹3 Crore with Smart Secure.",
        "precedents": [{
            "id": "00000000-0000-0000-0000-000000000002",
            "document_id": "t-uw",
            "anchor_text": "₹3 Crore",
            "comment_text": "Has UW approved this? Pls share approval on tool",
            "violation_category": "legal language",
            "severity": "critical",
            "score": 0.9,
        }],
        "expect_action": "share-evidence",
        "expect_evidence": True,
    },
    {
        "key": "gst-novel",
        "text": "GST is not applicable on individual life insurance premium as "
                "per Government Notification 16/2025.",
        "precedents": [],  # novel-only mode
        "expect_novel": True,
    },
]


async def _grade(chunk):
    from app.services.agents.graph.nodes import map_findings_to_violations
    from app.services.llm_service import llm_service
    from app.services.preprocessing_service import ContextEngineeringService
    from app.schemas.compliance_schemas import PrecedentCitationsResult

    ctx = ContextEngineeringService(db=None)
    prompt = ctx.create_precedent_prompts(chunk["text"], chunk["precedents"])
    result = await llm_service.generate_structured_response(
        prompt=prompt,
        output_model=PrecedentCitationsResult,
        system_prompt="You are a senior Bajaj Allianz compliance reviewer. JSON only.",
        temperature=0.0,
    )
    return map_findings_to_violations(
        result, chunk["precedents"],
        chunk_id=chunk["key"], chunk_index=0, location=f"chunk:{chunk['key']}",
    )


def test_smart_secure_golden():
    saw_novel_with_basis = False

    for chunk in CHUNKS:
        vios = asyncio.run(_grade(chunk))
        assert vios, f"no violations produced for chunk {chunk['key']}"

        for v in vios:
            desc = (v["description"] or "").lower()
            for bad in FORBIDDEN:
                assert bad not in desc, f"meta-bridge '{bad}' in {chunk['key']}: {v['description']}"

        md = vios[0]["violation_metadata"]

        if chunk.get("expect_substring"):
            joined = " ".join(v["description"] for v in vios)
            assert chunk["expect_substring"] in joined, (
                f"{chunk['key']}: expected '{chunk['expect_substring']}' in {joined!r}"
            )

        if chunk.get("expect_action"):
            actions = {v["violation_metadata"].get("action_type") for v in vios}
            assert chunk["expect_action"] in actions, (
                f"{chunk['key']}: expected action {chunk['expect_action']} in {actions}"
            )

        if chunk.get("expect_evidence"):
            assert any(v["violation_metadata"].get("evidence_needed") for v in vios), (
                f"{chunk['key']}: expected a non-null evidence_needed"
            )

        if chunk.get("expect_novel"):
            novels = [v for v in vios if v["violation_metadata"].get("grounding") == "novel"]
            assert novels, f"{chunk['key']}: expected a novel finding"
            assert all(n["violation_metadata"].get("regulatory_basis") for n in novels)
            assert all(n["cited_precedent_id"] is None for n in novels)
            saw_novel_with_basis = True

    assert saw_novel_with_basis, "no chunk produced a regulatory-grounded novel finding"
