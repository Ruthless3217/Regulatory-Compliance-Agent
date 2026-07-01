"""Generator-Critic verification for compliance violations.

The primary analysis LLM emits violations. The critic is a second LLM call
that independently reviews each violation against the rule it cites and
votes keep / downgrade / drop. This catches:

 - hallucinated rule IDs (rule_id not in the input set)
 - violations the primary invented without textual evidence
 - over-aggressive flagging of stylistic preferences as compliance breaches

Cost: one extra LLM call per (chunk × category) batch — roughly doubles the
analysis spend. For financial-grade output this is the right tradeoff.

Output contract: the critic returns the SAME list of violations, possibly
with reduced confidence (or dropped entirely if the critic strongly
disagrees). Downstream persistence is unchanged.

NOTE (architect-audit C7): this rule-based LLM critic is for a rule_id-driven
analysis path. The current PRECEDENT path emits rule_id=None, so it instead
uses the deterministic ``verify_evidence_grounding`` check in graph/nodes.py
(drops violations whose cited current_text isn't in the chunk). This module
remains the critic for the rules path and is exercised there.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from app.services.llm_service import critic_llm_service

logger = logging.getLogger(__name__)

# Drop threshold — if critic's confidence in a finding falls below this,
# we drop it entirely rather than persist as low-confidence.
_DROP_BELOW = 0.40
# Downgrade ceiling — keeper findings can't exceed critic_confidence.
# i.e. if primary said 0.95 and critic said 0.60, we persist 0.60.

try:
 from langsmith import traceable
except Exception: # pragma: no cover
 def traceable(*_a, **_kw): # type: ignore
 def _d(fn): return fn
 return _d if not (_a and callable(_a[0])) else _a[0]


class CritiqueItem(BaseModel):
 index: int = Field(..., description="Index of the violation in the input list (0-based)")
 keep: bool = Field(..., description="Whether this is a real, defensible violation")
 confidence: float = Field(0.5, ge=0.0, le=1.0, description="Critic confidence in keep/drop verdict")
 reason: str = Field("", description="One-sentence justification")


class CritiqueResult(BaseModel):
 critiques: List[CritiqueItem] = Field(default_factory=list)


def _build_critic_prompt(
 chunk_text: str,
 rules: List[Dict[str, Any]],
 violations: List[Dict[str, Any]],
) -> str:
 rules_block = "\n".join(
 f"- (ID: {r.get('id')}) [{r.get('severity', '?')}] {r.get('rule_text', '')}"
 for r in rules
 ) or "(none)"

 lines = []
 for i, v in enumerate(violations):
 meta = v.get("violation_metadata") or {}
 grounding = meta.get("grounding", "novel")
 head = (
 f"[{i}] grounding={grounding} severity={v.get('severity')} "
 f"primary_conf={v.get('confidence')}\n"
 f" description: {v.get('description', '')}\n"
 f" current_text: \"{(v.get('current_text') or '').strip()[:200]}\""
 )
 if grounding == "rule":
 evidence = (
 f"\n cited rule_id={v.get('rule_id')} "
 f"regulator_quote: \"{(v.get('regulator_quote') or '').strip()[:200]}\"\n"
 f" CHECK: does current_text breach this exact rule, with the breach "
 f"visible verbatim in the document?"
 )
 elif grounding == "precedent":
 evidence = (
 f"\n cited precedent (reviewer's historical note): "
 f"\"{(v.get('cited_comment_verbatim') or '').strip()[:200]}\"\n"
 f" CHECK: does the precedent genuinely apply to current_text "
 f"(same issue, present verbatim in the document)?"
 )
 else: # novel
 evidence = (
 "\n no rule / no precedent (model's own judgment)\n"
 " CHECK: is this a real, defensible compliance breach grounded "
 "verbatim in the document — NOT a stylistic preference?"
 )
 lines.append(head + evidence)
 viol_block = "\n".join(lines)

 return f"""You are an independent compliance critic. Another LLM produced the
violation list below across three grounding tiers (rule / precedent / novel).
Verify each one against the actual document text and its cited evidence.
Be skeptical: stylistic preferences and vague claims are NOT compliance violations.

For each violation decide:
- keep=true if it is a real, defensible breach with evidence verbatim in the document
- keep=false if the cited rule/precedent doesn't apply, the cited text isn't in the
 document, or the violation is invented / stretched / merely stylistic

Provide a 0.0-1.0 confidence in your OWN verdict. Use <0.4 only when you are fairly
sure the primary LLM hallucinated — those will be dropped.

DOCUMENT (chunk):
{chunk_text[:4000]}

INPUT RULES (only these rule_ids are valid for rule-tier findings):
{rules_block}

PRIMARY VIOLATIONS TO REVIEW:
{viol_block}

Return JSON with one critique per input violation, in input order."""


@traceable(run_type="llm", name="Critic.review_violations")
async def critique_violations(
 chunk_text: str,
 rules: List[Dict[str, Any]],
 violations: List[Dict[str, Any]],
 precedents: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
 """Filter / downgrade the violation list via an independent LLM check.

 Returns the new violation list. Never raises — on critic failure the
 original violations pass through unchanged so the pipeline is resilient.

 The ``precedents`` param is accepted for symmetry/future use; per-violation
 evidence is read directly off each violation dict (cited_comment_verbatim,
 cited_anchor_text, regulator_quote) rather than a separate precedents list.
 """
 if not violations:
 return violations
 try:
 result = await critic_llm_service.generate_structured_response(
 prompt=_build_critic_prompt(chunk_text, rules, violations),
 output_model=CritiqueResult,
 system_prompt=(
 "You are a meticulous compliance auditor. You ONLY validate "
 "violations against the provided rules, precedents and document "
 "text. Return strict JSON."
 ),
 tool_name="critic_review",
 # Cheap/fast second pass: route to LLM_CLASSIFY_MODEL (gpt-5.4-nano)
 # when configured. The heavy citation/grading call keeps LLM_MODEL.
 model=critic_llm_service.model,
 )
 except Exception as e:
 logger.warning(f"Critic call failed; passing primary violations through: {e}")
 return violations

 keep_by_index: Dict[int, CritiqueItem] = {c.index: c for c in result.critiques}
 surviving: List[Dict[str, Any]] = []
 dropped = downgraded = 0
 for i, v in enumerate(violations):
 c = keep_by_index.get(i)
 if c is None:
 # No critic verdict — keep but mark low confidence so reviewers
 # know it didn't pass through the critic loop.
 surviving.append(v)
 continue
 is_critical = str(v.get("severity", "")).strip().lower() == "critical"
 if not c.keep and c.confidence < _DROP_BELOW and not is_critical:
 meta = v.get("violation_metadata") or {}
 grounding = meta.get("grounding", "unknown")
 logger.info(
 f"Critic dropped violation {i} "
 f"(rule_id={v.get('rule_id')}, grounding={grounding}, critic_conf={c.confidence:.2f}): {c.reason}"
 )
 dropped += 1
 continue
 # Confidence adjustment logic:
 # keepers → min(primary, critic_conf) caps primary at critic's certainty
 # non-dropped non-critical rejects → critic_conf * 0.5 conservative floor, not a placeholder
 # criticals → primary confidence preserved (never weakened by critic)
 try:
 primary_conf = float(v.get("confidence", 0.85))
 except (TypeError, ValueError):
 primary_conf = 0.85
 if is_critical:
 new_conf = primary_conf
 else:
 new_conf = round(min(primary_conf, c.confidence), 3) if c.keep else round(c.confidence * 0.5, 3)
 if new_conf != primary_conf:
 downgraded += 1
 v_out = dict(v)
 v_out["confidence"] = new_conf
 # Stash the critic's reason for downstream auditability
 existing_meta = v_out.get("violation_metadata") or {}
 if isinstance(existing_meta, dict):
 existing_meta = {**existing_meta, "critic_reason": c.reason, "critic_confidence": c.confidence}
 v_out["violation_metadata"] = existing_meta
 surviving.append(v_out)

 if dropped or downgraded:
 logger.info(
 f"Critic pass: {len(violations)} in → {len(surviving)} out "
 f"(dropped={dropped}, downgraded={downgraded})"
 )
 return surviving
