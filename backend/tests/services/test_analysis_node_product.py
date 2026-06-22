# backend/tests/services/test_analysis_node_product.py
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

from app.schemas.compliance_schemas import PrecedentCitationsResult, ProductFactFinding
from app.services.agents.graph import nodes


CARD = {"uin": "116N198V07", "product_name": "Bajaj Life eTouch II",
        "compliance_guardrails": {"claims_marketing_must_avoid": ["Do NOT use 'guaranteed returns'."]}}


def _state():
    return {
        "submission_id": None,
        "user_id": None,
        "chunks": [{"id": "c1", "chunk_index": 0, "text": "Get guaranteed returns now!", "metadata": {}}],
        "retrieved_examples": {"c1": []},
        "chunk_rules": {},
        "active_rules": {},
        "metadata": {},
        "product_facts": [CARD],
        "product_passages": {"c1": []},
    }


def test_product_fact_finding_becomes_violation():
    result = PrecedentCitationsResult(product_fact_findings=[ProductFactFinding(
        product_index=0,
        reviewer_comment="Remove 'guaranteed returns' — eTouch is non-par term.",
        guardrail_text="Do NOT use 'guaranteed returns'.",
        finding_kind="banned-claim", action_type="remove",
        current_text="guaranteed returns", severity="critical",
    )])

    with patch("app.database.SessionLocal", MagicMock()), \
         patch("app.services.llm_service.llm_service.generate_structured_response",
               new=AsyncMock(return_value=result)), \
         patch("app.services.preprocessing_service.ContextEngineeringService") as CES, \
         patch("app.services.agents.validators.validate_agent_output", return_value=(True, [])):
        CES.return_value.create_precedent_prompts.return_value = "PROMPT"
        CES.return_value.create_completeness_sweep_prompt.return_value = "SWEEP"
        out = asyncio.run(nodes.analysis_node(_state()))

    groundings = [v["violation_metadata"]["grounding"] for v in out["violations"]]
    assert "product_fact" in groundings


def test_create_prompt_receives_product_facts():
    captured = {}

    def _cap(content, precedents, rules=None, document_context=None,
             product_facts=None, product_passages=None):
        captured["product_facts"] = product_facts
        captured["product_passages"] = product_passages
        return "PROMPT"

    with patch("app.database.SessionLocal", MagicMock()), \
         patch("app.services.llm_service.llm_service.generate_structured_response",
               new=AsyncMock(return_value=PrecedentCitationsResult())), \
         patch("app.services.preprocessing_service.ContextEngineeringService") as CES:
        CES.return_value.create_precedent_prompts.side_effect = _cap
        CES.return_value.create_completeness_sweep_prompt.return_value = "SWEEP"
        asyncio.run(nodes.analysis_node(_state()))

    assert captured["product_facts"] == [CARD]
    assert captured["product_passages"] == []
