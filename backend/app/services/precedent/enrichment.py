"""Cheap, cached, resumable LLM enrichment for a canonical precedent.

Produces a normalized issue_type, a one-line rationale, an optional guideline
reference, and an LLM-judged severity. Cached one JSON file per cache key so a
full rebuild is quota-safe and free to re-run.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.services.llm_service import critic_llm_service

logger = logging.getLogger(__name__)

_SEVERITIES = {"critical", "moderate", "informational"}

# Guideline doc stems the model may reference (docs/guidelines-docs/*).
_GUIDELINE_HINTS = (
    "ulip_compliance_guidelines, term_insurance_compliance_guidelines, "
    "tax_and_gst_compliance_guidelines, retirement_and_pension_compliance_guidelines"
)

_SYSTEM = (
    "You normalize a single historical insurance-marketing compliance review "
    "comment into structured fields. Be terse and factual. Do not invent "
    "regulations. If no guideline family clearly applies, return null for "
    "guideline_ref."
)


class PrecedentEnrichment(BaseModel):
    issue_type: str = Field(description="Short canonical name for the kind of issue, Title Case")
    why_rationale: str = Field(description="One sentence: why this was flagged")
    guideline_ref: Optional[str] = Field(
        default=None,
        description=f"One of these stems if applicable, else null: {_GUIDELINE_HINTS}",
    )
    severity: str = Field(description="critical | moderate | informational")


def _prompt(record: Dict[str, Any]) -> str:
    return (
        f"Highlighted span: {record.get('highlighted_span','')}\n"
        f"Surrounding context: {record.get('span_context','')}\n"
        f"Reviewer comment (verbatim): {record.get('reviewer_comment','')}\n"
        f"Reviewer role: {record.get('reviewer_role','')}\n"
        f"Topic tags (heuristic): {', '.join(record.get('issue_seed_tags') or [])}\n\n"
        f"Guideline families: {_GUIDELINE_HINTS}\n"
        "Return issue_type, why_rationale, guideline_ref (or null), severity."
    )


def _coerce(out: PrecedentEnrichment) -> PrecedentEnrichment:
    sev = (out.severity or "").strip().lower()
    if sev not in _SEVERITIES:
        sev = "moderate"
    return out.model_copy(update={"severity": sev})


async def enrich(record: Dict[str, Any], cache_dir: str) -> PrecedentEnrichment:
    os.makedirs(cache_dir, exist_ok=True)
    key = record["_cache_key"]
    path = os.path.join(cache_dir, f"{key}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return PrecedentEnrichment(**json.load(f))

    out = await critic_llm_service.generate_structured_response(
        prompt=_prompt(record),
        output_model=PrecedentEnrichment,
        system_prompt=_SYSTEM,
        tool_name="precedent_enrichment",
        temperature=0.0,
    )
    out = _coerce(out)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out.model_dump(), f, ensure_ascii=False)
    os.replace(tmp, path)  # atomic write
    return out
