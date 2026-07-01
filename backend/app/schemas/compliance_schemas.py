from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class ViolationSchema(BaseModel):
 id: Optional[str] = Field(None, description="Persisted violation UUID (set after persist)")
 category: str = Field(..., description="Category of the violation: one of terminology issue, legal language, missing reference, disclaimer issue, other")
 severity: str = Field(..., description="Severity level: one of critical, moderate, informational")
 rule_id: Optional[str] = Field(None, description="The ID of the violated rule — MUST be one of the rule_id values shown in the input rules block")
 description: str = Field(..., description="Brief description of the violation")
 location: Optional[str] = Field(None, description="Location reference in the text")
 current_text: Optional[str] = Field(..., description="The exact problematic phrase from the submission — copy verbatim, no paraphrase")
 suggested_fix: Optional[str] = Field(None, description="Suggested compliant rewrite of current_text")
 auto_fixable: bool = Field(False, description="Whether this can be auto-fixed by a simple string substitution")
 chunk_index: Optional[int] = Field(None, description="Index of the content chunk")
 # P1.3 — Confidence + citation are required for audit-defensible findings
 confidence: float = Field(
 0.85,
 ge=0.0,
 le=1.0,
 description="0.0–1.0 confidence that this is a real violation of the cited rule. Use <0.6 if uncertain."
 )
 regulator_quote: Optional[str] = Field(
 None,
 description="Verbatim quote from the rule's regulator passage (≤200 chars). Leave null only if no source passage exists in the input."
 )

 # Precedent-citation fields (Phase 1.5). Populated when the violation was
 # produced by the precedent path, where the analyzer matches a retrieved
 # historical reviewer comment to the new section and quotes it verbatim.
 # Reviewer-name fields are intentionally omitted from the citation surface.
 cited_precedent_id: Optional[str] = Field(
 None,
 description="UUID of the rag_compliance_examples row this violation cites."
 )
 cited_document_id: Optional[str] = Field(
 None,
 description="Original ticket/document id of the cited precedent."
 )
 cited_source_file: Optional[str] = Field(
 None,
 description="Source filename of the cited precedent JSON, for provenance."
 )
 cited_anchor_text: Optional[str] = Field(
 None,
 description="The exact reviewer-highlighted phrase from the precedent."
 )
 cited_comment_verbatim: Optional[str] = Field(
 None,
 description="The reviewer's comment text from the precedent, copied verbatim."
 )
 cited_final_text: Optional[str] = Field(
 None,
 description="The approved rewrite excerpt from the precedent's final document, if available."
 )
 similarity_score: Optional[float] = Field(
 None,
 description="Retrieval score (cosine + BM25 RRF) of the cited precedent for this section."
 )


class ComplianceAnalysisResult(BaseModel):
 violations: List[ViolationSchema] = Field(
 default_factory=list,
 description="List of detected violations"
 )
 overall_assessment: str = Field(
 ...,
 description="Brief summary of compliance status"
 )
 key_issues: List[str] = Field(
 default_factory=list,
 description="List of key issues identified"
 )


# --- Reviewer-voice commentary + novel-finding coverage (2026-05-28) -----

# Action buckets the reviewer picks per finding. Surfaced in the UI as a
# colour-coded badge; lives in violation_metadata.action_type (no DB migration).
ACTION_TYPES = Literal[
 "rewrite", # use standardized terminology or insert prescribed text
 "share-evidence", # produce an approval/source artifact (UW / Tax / PO / BI)
 "add-disclaimer", # insert a missing regulatory disclaimer
 "verify-source", # clarify provenance, match against authoritative document
 "remove", # strip out a non-compliant claim
]


class PrecedentCitation(BaseModel):
 """The LLM cites a retrieved precedent. `reviewer_comment` is the LLM's
 adaptation of the historical reviewer's substance + tone onto the NEW
 section — written as the reviewer would write it, never as meta-commentary
 on the precedent. Severity/category/anchor/final-text are still carried
 over from the retrieved precedent by the application code."""

 precedent_index: int = Field(
 ...,
 ge=0,
 description="Zero-based index into the input precedents list of the precedent that applies.",
 )
 current_text: str = Field(
 ...,
 min_length=1,
 description="EXACT phrase from the NEW section this precedent flags. Copy verbatim from the new section — no paraphrase. Used by the UI to highlight the offending span.",
 )
 reviewer_comment: str = Field(
 ...,
 min_length=10,
 description=(
 "1-2 sentences in reviewer voice (imperative/interrogative). "
 "Names the offending phrase, states WHY it is non-compliant (the rule "
 "it breaks, the disclosure it omits, or the claim it leaves "
 "unsubstantiated), and includes the specifics the precedent supplied: "
 "exact prescribed text, section names, charges, conditions. Never just "
 "observe that a topic 'appears' or is 'similar' — that is not a reason. "
 "Never meta — never 'similar to a precedent that…' or 'the precedent flagged…'."
 ),
 )
 action_type: ACTION_TYPES = Field(
 ...,
 description="One of rewrite | share-evidence | add-disclaimer | verify-source | remove.",
 )
 evidence_needed: Optional[str] = Field(
 None,
 description=(
 "Short noun phrase if an external artifact is named, e.g. 'UW approval', "
 "'latest fact sheet', 'Tax team approval'. Null when action_type is "
 "rewrite/remove without a named artifact."
 ),
 )
 confidence: float = Field(
 0.85,
 ge=0.0,
 le=1.0,
 description="0.0–1.0 confidence that a senior reviewer would flag this in the new section as the cited precedent does.",
 )
 satisfied_elsewhere: bool = Field(
 False,
 description=(
 "Set TRUE only for a MISSING-disclaimer / MISSING-footnote / "
 "MISSING-reference finding whose required element ALREADY appears "
 "elsewhere in the DOCUMENT CONTEXT. NEVER set it for a substantive "
 "issue (a claim, guarantee, superlative, misleading or solicitation "
 "phrase) — context cannot cure those. Flagged findings are kept for "
 "audit, not dropped; critical findings are never suppressed."
 ),
 )


class NovelFinding(BaseModel):
 """Issue clearly present in the new section but NOT covered by any retrieved
 precedent. Requires regulatory grounding and a higher confidence floor —
 there is no historical reviewer to point at, so the comment carries the
 rationale itself."""

 current_text: str = Field(
 ...,
 min_length=1,
 description="EXACT phrase from the NEW section this finding flags. Verbatim, no paraphrase.",
 )
 reviewer_comment: str = Field(
 ...,
 min_length=20,
 description=(
 "2-4 sentences: flag + reasoning + specific action. Longer than "
 "precedent-grounded comments because there is no historical reviewer "
 "to point at — this comment carries the rationale."
 ),
 )
 action_type: ACTION_TYPES = Field(
 ...,
 description="One of rewrite | share-evidence | add-disclaimer | verify-source | remove.",
 )
 evidence_needed: Optional[str] = Field(None)
 regulatory_basis: str = Field(
 ...,
 min_length=10,
 description=(
 "Specific rule/section the issue violates. Examples: "
 "'IRDAI ULIP regulations — Miscellaneous Charge disclosure', "
 "'Section 41 Insurance Act — no rebate/inducement claims', "
 "'IRDAI Advertisement Regulations 2021 — past performance disclaimer'."
 ),
 )
 confidence: float = Field(
 ...,
 ge=0.0,
 le=1.0,
 description="0.0–1.0 confidence. Novel findings below the 0.75 floor are dropped before persistence.",
 )
 satisfied_elsewhere: bool = Field(
 False,
 description=(
 "Set TRUE only for a MISSING-disclaimer / MISSING-footnote / "
 "MISSING-reference finding whose required element ALREADY appears "
 "elsewhere in the DOCUMENT CONTEXT. NEVER set it for a substantive "
 "issue — context cannot cure a bad claim. Kept for audit, not dropped."
 ),
 )


class RuleFinding(BaseModel):
 """Tier-2 (rule-grounded) finding: the section violates a retrieved
 regulatory RULE even though no precedent flagged it. The rule supplies the
 citation (rule_id + regulator passage); the LLM supplies the on-document
 reviewer_comment. Stronger grounding than a novel finding, weaker than a
 precedent (no human reviewer decided this exact case)."""

 rule_index: int = Field(
 ...,
 ge=0,
 description="Zero-based index into the input RULES list of the rule that applies.",
 )
 current_text: str = Field(
 ...,
 min_length=1,
 description="EXACT phrase from the NEW section the rule flags. Verbatim, no paraphrase.",
 )
 reviewer_comment: str = Field(
 ...,
 min_length=10,
 description=(
 "1-2 sentences in reviewer voice naming the offending phrase and what "
 "the rule requires (the disclosure to add, claim to remove, term to "
 "standardize). Never meta — write as the reviewer, not about the rule."
 ),
 )
 action_type: ACTION_TYPES = Field(
 ...,
 description="One of rewrite | share-evidence | add-disclaimer | verify-source | remove.",
 )
 evidence_needed: Optional[str] = Field(None)
 confidence: float = Field(
 0.85,
 ge=0.0,
 le=1.0,
 description="0.0–1.0 confidence that this section actually violates the cited rule.",
 )
 satisfied_elsewhere: bool = Field(
 False,
 description=(
 "Set TRUE only for a MISSING-disclaimer / MISSING-footnote / "
 "MISSING-reference finding whose required element ALREADY appears "
 "elsewhere in the DOCUMENT CONTEXT. NEVER set it for a substantive "
 "issue — context cannot cure a bad claim. Kept for audit, not dropped."
 ),
 )


class ProductFactFinding(BaseModel):
 """Product-grounded finding: the section violates the matched product's
 curated fact-card guardrail (a banned claim, a missing mandatory element, a
 wrong/absent regulatory descriptor, or a claim that is valid only for a
 specific variant). Authoritative + deterministic — the guardrail is verbatim
 from the approved fact card. Hard-leaning: a present `claims_marketing_must_avoid`
 item IS a finding."""

 product_index: int = Field(
 ...,
 ge=0,
 description="Zero-based index into the input PRODUCT FACTS list of the product whose guardrail is violated.",
 )
 current_text: str = Field(
 "",
 description="EXACT offending phrase from the NEW section, verbatim. Empty ONLY for a missing-mandatory finding (nothing to quote).",
 )
 reviewer_comment: str = Field(
 ...,
 min_length=10,
 description="1-2 sentences in reviewer voice: name the offending phrase (or the missing element), and state the product-specific reason from the guardrail.",
 )
 guardrail_text: str = Field(
 ...,
 min_length=1,
 description="The fact-card guardrail line being applied, copied verbatim.",
 )
 finding_kind: Literal["banned-claim", "missing-mandatory", "wrong-descriptor", "unqualified-claim"] = Field(
 ...,
 description="banned-claim (a must_avoid claim present) | missing-mandatory (a must_state element absent) | wrong-descriptor (regulatory descriptor wrong/absent) | unqualified-claim (a must_support claim not variant-qualified).",
 )
 action_type: ACTION_TYPES = Field(
 ...,
 description="One of rewrite | share-evidence | add-disclaimer | verify-source | remove.",
 )
 evidence_needed: Optional[str] = Field(
 None,
 description="Named artifact (e.g. a specific IRDAI circular or approved source) the reviewer should obtain before acting. None when not required.",
 )
 severity: Literal["critical", "moderate", "informational"] = Field(
 "moderate",
 description="banned-claim / wrong-descriptor → critical; unqualified-claim / missing-mandatory → moderate.",
 )
 confidence: float = Field(
 0.85,
 ge=0.0,
 le=1.0,
 description="0.0–1.0 confidence the section actually violates this product guardrail.",
 )
 satisfied_elsewhere: bool = Field(
 False,
 description="Set TRUE only for a MISSING mandatory element that ALREADY appears elsewhere in DOCUMENT CONTEXT. Never for a banned/unqualified claim.",
 )


class PrecedentCitationsResult(BaseModel):
 citations: List[PrecedentCitation] = Field(
 default_factory=list,
 description="Citations of historic precedents that apply to this section. Emit one entry PER applicable precedent — multiple precedents can apply to the same section. Omit precedents that do not apply.",
 )
 rule_findings: List[RuleFinding] = Field(
 default_factory=list,
 description="Violations of a retrieved regulatory RULE not already covered by a precedent citation. Emit one entry PER applicable rule. Omit rules that do not apply.",
 )
 novel_findings: List[NovelFinding] = Field(
 default_factory=list,
 description="Issues clearly present in this section that NO listed precedent OR rule covers. Each REQUIRES a regulatory_basis and confidence ≥ 0.75. Do not invent findings.",
 )
 product_fact_findings: List[ProductFactFinding] = Field(
 default_factory=list,
 description="Violations of the matched product's fact-card guardrails (banned claims, missing mandatory elements, wrong descriptor, variant-unqualified claims). Emit one entry per applicable guardrail. Empty when no product was matched.",
 )
