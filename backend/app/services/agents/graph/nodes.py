"""
LangGraph Nodes for Regulatory Compliance Workflow.

Graph flow:
    start → preprocess_node → dispatch_node → analysis_node → scoring_node → refinement_node → end

Each node is a pure async function that takes ComplianceState and returns partial state updates.
"""
import logging
import uuid
import datetime
import asyncio
from typing import Dict, Any, List, Optional
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

try:
    from langsmith import traceable
except Exception:  # pragma: no cover
    def traceable(*_a, **_kw):  # type: ignore
        def _d(fn): return fn
        return _d if not (_a and callable(_a[0])) else _a[0]

from .state import ComplianceState

logger = logging.getLogger(__name__)

# Novel findings below this confidence are dropped before persistence — there
# is no historical reviewer to back them, so the bar is higher than for
# precedent-grounded citations (see 2026-05-28 reviewer-voice design).
NOVEL_CONFIDENCE_FLOOR = 0.75

# Severity is hard-coded for novel findings in v1 (open question in the design:
# should it be LLM-emitted?). Category is generic since novel findings carry
# their grounding in violation_metadata.regulatory_basis, not a precedent.
_NOVEL_SEVERITY = "moderate"
_NOVEL_CATEGORY = "regulatory"

# Rules carry critical/high/medium/low; the precedent-path validator only
# accepts critical/moderate/informational. Map conservatively (high→moderate so
# rule findings don't inflate the critical count that drives fail-closed/scoring).
_RULE_SEVERITY_MAP = {
    "critical": "critical",
    "high": "moderate",
    "medium": "moderate",
    "low": "informational",
    "moderate": "moderate",
    "informational": "informational",
}

# Cap rules per chunk fed to the LLM (token control); rules carry a
# regulator_quote from dispatch_node enrichment.
_MAX_RULES_PER_CHUNK = 8


def _select_rules_for_chunk(
    chunk_id,
    chunk_rules: Dict[str, Dict[str, List[Dict]]],
    active_rules: Dict[str, List[Dict]],
    rag_degraded: bool,
    max_rules: int = _MAX_RULES_PER_CHUNK,
) -> List[Dict]:
    """Choose the Tier-2 rules to feed the LLM for one chunk.

    Normal path: use the per-chunk RAG-retrieved rules (already ranked/filtered).
    A chunk that legitimately matched no rules returns [] — we do NOT dump the
    whole active set back in, which would re-introduce the noise retrieval
    filtered out.

    Degraded path (rag_degraded): per-chunk retrieval failed, so chunk_rules is
    empty. Fall back to the FLAT active-rule set so the rule tier still runs —
    otherwise the document is graded on precedent+novel alone and persisted as a
    real grade (silent false negatives). See full-pipeline audit 2026-06-16.
    """
    if rag_degraded:
        flat: List[Dict] = []
        for rule_list in (active_rules or {}).values():
            flat.extend(rule_list or [])
        return flat[:max_rules]

    by_cat = (chunk_rules or {}).get(str(chunk_id)) or {}
    flat = []
    for rule_list in by_cat.values():
        flat.extend(rule_list or [])
    return flat[:max_rules]


def _citation_to_violation(
    c: Any, precedent: Dict[str, Any], *, chunk_id, chunk_index, location: str
) -> Dict[str, Any]:
    """Map one precedent citation to a violation dict. Severity/category/anchor/
    comment-verbatim/final-text are carried over from the retrieved precedent;
    the LLM supplies only the on-document reviewer_comment, action_type and
    evidence_needed (which land in violation_metadata)."""
    p = precedent
    sev = p.get("severity") or "informational"
    satisfied = bool(getattr(c, "satisfied_elsewhere", False))
    # Scoped suppression: a satisfied-elsewhere finding is kept (audit lane) but
    # not scored — EXCEPT a critical, which is never suppressed (false negatives
    # on criticals are the worst failure mode for a fail-closed compliance tool).
    suppressed = satisfied and sev != "critical"
    return {
        "category": p.get("violation_category") or "other",
        "severity": sev,
        "suppressed": suppressed,
        "suppressed_reason": (
            "satisfied_elsewhere: required element already present elsewhere in the document"
            if suppressed else None
        ),
        "description": (c.reviewer_comment or "").strip(),
        "current_text": (c.current_text or "").strip(),
        "suggested_fix": p.get("final_text_chunk") or None,
        "auto_fixable": False,
        "confidence": float(c.confidence if c.confidence is not None else 0.85),
        "rule_id": None,
        "regulator_quote": None,
        # Citation columns — provenance back to the precedent.
        "cited_precedent_id": p.get("id"),
        "cited_document_id": p.get("document_id"),
        "cited_source_file": p.get("source_file"),
        "cited_anchor_text": p.get("anchor_text"),
        "cited_comment_verbatim": p.get("comment_text"),
        "cited_final_text": p.get("final_text_chunk"),
        # Precedent KB v2 enrichment — the reviewer's "why" + guideline family,
        # for traceability alongside the prompt-surfaced rationale.
        "cited_why_rationale": p.get("why_rationale"),
        "cited_guideline_ref": p.get("guideline_ref"),
        "similarity_score": p.get("score"),
        "chunk_id": str(chunk_id),
        "chunk_index": chunk_index,
        "location": location,
        "violation_metadata": {
            "grounding": "precedent",
            "action_type": c.action_type,
            "evidence_needed": c.evidence_needed,
            "satisfied_elsewhere": satisfied,
        },
    }


def _novel_finding_to_violation(
    f: Any, *, chunk_id, chunk_index, location: str
) -> Dict[str, Any]:
    """Map one novel finding to a violation dict.

    A novel finding below NOVEL_CONFIDENCE_FLOOR is NOT dropped silently (that
    would be an undetectable false negative — exactly the dangerous case in a
    recall-critical compliance tool). It is persisted with ``suppressed=True``
    and a reason, kept out of the score, and surfaced to a human review lane.
    Citation columns are NULL; grounding/regulatory_basis live in metadata.
    """
    conf = float(f.confidence)
    satisfied = bool(getattr(f, "satisfied_elsewhere", False))
    below_floor = conf < NOVEL_CONFIDENCE_FLOOR
    # Novel findings are never critical (_NOVEL_SEVERITY), so satisfied-elsewhere
    # always suppresses them into the audit lane; sub-floor findings stay
    # suppressed as before. Either way: persisted + visible, never dropped.
    suppressed = below_floor or satisfied
    if suppressed:
        logger.info(
            "Novel finding suppressed (below_floor=%s satisfied_elsewhere=%s, "
            "conf %.2f) — persisting for human review, not scored: %r",
            below_floor, satisfied, conf, (f.reviewer_comment or "")[:120],
        )
    return {
        "category": _NOVEL_CATEGORY,
        "severity": _NOVEL_SEVERITY,
        "description": (f.reviewer_comment or "").strip(),
        "current_text": (f.current_text or "").strip(),
        "suggested_fix": None,
        "auto_fixable": False,
        "confidence": conf,
        "rule_id": None,
        "regulator_quote": None,
        "cited_precedent_id": None,
        "cited_document_id": None,
        "cited_source_file": None,
        "cited_anchor_text": None,
        "cited_comment_verbatim": None,
        "cited_final_text": None,
        "cited_why_rationale": None,
        "cited_guideline_ref": None,
        "similarity_score": None,
        "suppressed": suppressed,
        "suppressed_reason": (
            "satisfied_elsewhere: required element already present elsewhere in the document"
            if satisfied else
            f"novel finding confidence {conf:.2f} below floor {NOVEL_CONFIDENCE_FLOOR}"
            if below_floor else None
        ),
        "chunk_id": str(chunk_id),
        "chunk_index": chunk_index,
        "location": location,
        "violation_metadata": {
            "grounding": "novel",
            "action_type": f.action_type,
            "evidence_needed": f.evidence_needed,
            "regulatory_basis": f.regulatory_basis,
            "satisfied_elsewhere": satisfied,
        },
    }


def _rule_finding_to_violation(
    f: Any, rule: Dict[str, Any], *, chunk_id, chunk_index, location: str
) -> Dict[str, Any]:
    """Map one rule-grounded finding to a violation dict. The rule supplies the
    citation: rule_id + regulator_quote (verbatim passage). Citation columns
    stay NULL (no precedent); grounding='rule' lives in violation_metadata."""
    raw_sev = str(rule.get("severity") or "").strip().lower()
    severity = _RULE_SEVERITY_MAP.get(raw_sev, "moderate")
    satisfied = bool(getattr(f, "satisfied_elsewhere", False))
    # Rule severity is capped at moderate (never critical), so satisfied-elsewhere
    # always routes to the audit lane; the != critical guard is kept for symmetry.
    suppressed = satisfied and severity != "critical"
    return {
        "category": rule.get("category") or _NOVEL_CATEGORY,
        "severity": severity,
        "suppressed": suppressed,
        "suppressed_reason": (
            "satisfied_elsewhere: required element already present elsewhere in the document"
            if suppressed else None
        ),
        "description": (f.reviewer_comment or "").strip(),
        "current_text": (f.current_text or "").strip(),
        "suggested_fix": None,
        "auto_fixable": False,
        "confidence": float(f.confidence if f.confidence is not None else 0.85),
        # Rule provenance — resolved to a UUID + verbatim passage downstream.
        "rule_id": rule.get("id"),
        "regulator_quote": rule.get("source_quote") or rule.get("regulator_quote"),
        "cited_precedent_id": None,
        "cited_document_id": None,
        "cited_source_file": None,
        "cited_anchor_text": None,
        "cited_comment_verbatim": None,
        "cited_final_text": None,
        "cited_why_rationale": None,
        "cited_guideline_ref": None,
        "similarity_score": None,
        "chunk_id": str(chunk_id),
        "chunk_index": chunk_index,
        "location": location,
        "violation_metadata": {
            "grounding": "rule",
            "action_type": f.action_type,
            "evidence_needed": f.evidence_needed,
            "satisfied_elsewhere": satisfied,
        },
    }


def _product_fact_finding_to_violation(
    f: Any, card: Dict[str, Any], *, chunk_id, chunk_index, location: str
) -> Dict[str, Any]:
    """Map one product-fact finding to a violation dict. The fact card supplies
    the authoritative guardrail; provenance lives in violation_metadata under
    grounding='product_fact'. No rule_id / precedent columns."""
    severity = str(getattr(f, "severity", "moderate") or "moderate").strip().lower()
    if severity not in ("critical", "moderate", "informational"):
        severity = "moderate"
    satisfied = bool(getattr(f, "satisfied_elsewhere", False))
    suppressed = satisfied and severity != "critical"
    return {
        "category": "product compliance",
        "severity": severity,
        "suppressed": suppressed,
        "suppressed_reason": (
            "satisfied_elsewhere: required element already present elsewhere in the document"
            if suppressed else None
        ),
        "description": (f.reviewer_comment or "").strip(),
        "current_text": (getattr(f, "current_text", "") or "").strip(),
        "suggested_fix": None,
        "auto_fixable": False,
        "confidence": float(f.confidence if f.confidence is not None else 0.85),
        "rule_id": None,
        "regulator_quote": None,
        "cited_precedent_id": None,
        "cited_document_id": None,
        "cited_source_file": None,
        "cited_anchor_text": None,
        "cited_comment_verbatim": None,
        "cited_final_text": None,
        "cited_why_rationale": None,
        "cited_guideline_ref": None,
        "similarity_score": None,
        "chunk_id": str(chunk_id),
        "chunk_index": chunk_index,
        "location": location,
        "violation_metadata": {
            "grounding": "product_fact",
            "product_uin": card.get("uin"),
            "product_name": card.get("product_name"),
            "guardrail_text": (f.guardrail_text or "").strip(),
            "finding_kind": f.finding_kind,
            "action_type": f.action_type,
            "evidence_needed": getattr(f, "evidence_needed", None),
            "satisfied_elsewhere": satisfied,
        },
    }


def _disclosure_finding_to_violation(
    d: "Any", *, status: str, similarity: float, provenance: str, confidence: float
) -> Dict[str, Any]:
    """Map one missing/altered mandated disclaimer to a violation dict.

    Document-level (chunk_id=None, empty current_text): a disclaimer obligation
    is about the whole document, not a single span. The verbatim registry text
    is surfaced as suggested_fix — paste-ready, never paraphrased. Severity is
    the registry's per-disclaimer value (one notch lower for 'altered')."""
    severity = d.severity if status == "missing" else d.altered_severity
    if status == "missing":
        desc = f"Required disclaimer missing: {d.type}. ({provenance})"
    else:
        desc = (
            f"Disclaimer present but altered/incomplete: {d.type} "
            f"(similarity {similarity:.2f}). Replace with the approved wording. ({provenance})"
        )
    return {
        "category": "mandatory disclosure",
        "severity": severity,
        "suppressed": False,
        "suppressed_reason": None,
        "description": desc,
        "current_text": "",
        "suggested_fix": d.text,
        "auto_fixable": False,
        "confidence": float(confidence),
        "rule_id": None,
        "regulator_quote": None,
        "cited_precedent_id": None,
        "cited_document_id": None,
        "cited_source_file": None,
        "cited_anchor_text": None,
        "cited_comment_verbatim": None,
        "cited_final_text": None,
        "cited_why_rationale": None,
        "cited_guideline_ref": None,
        "similarity_score": float(similarity),
        "chunk_id": None,
        "chunk_index": 0,
        "location": "document",
        "violation_metadata": {
            "grounding": "disclosure",
            "disclaimer_id": d.id,
            "disclaimer_type": d.type,
            "match_status": status,
            "trigger_provenance": provenance,
        },
    }


class _ObligationResult(BaseModel):
    obligations: List[str] = Field(default_factory=list)


async def _disclosure_llm_call(document_text: str, obligation_types: List[str]) -> List[str]:
    """LLM backstop: which obligation types apply to this content? Returns a
    subset of obligation_types. Uses the critic profile (cheap, independent)."""
    from app.services.llm_service import critic_llm_service
    prompt = (
        "You classify which mandatory-disclaimer obligations apply to the marketing "
        "content below. Consider paraphrases, not just exact phrases (e.g. 'our fund "
        "grew 12% last year' implies a past-performance obligation).\n\n"
        f"Allowed obligation types: {obligation_types}\n\n"
        f"CONTENT:\n{document_text[:6000]}\n\n"
        "Return the obligation types that apply."
    )
    result = await critic_llm_service.generate_structured_response(
        prompt=prompt, output_model=_ObligationResult,
        tool_name="disclosure_backstop", temperature=0.0,
    )
    return [t for t in (result.obligations or []) if t in obligation_types]


def _normalize_ws(s: str) -> str:
    """Lowercase + collapse all whitespace runs to single spaces."""
    return " ".join((s or "").lower().split())


def merge_findings(
    citations: List[Any],
    rule_findings: List[Any],
    novel_findings: List[Any],
    add_citations: List[Any],
    add_rule_findings: List[Any],
    add_novel_findings: List[Any],
    product_fact_findings: Optional[List[Any]] = None,
    add_product_fact_findings: Optional[List[Any]] = None,
) -> tuple:
    """Merge completeness-sweep findings into the first-pass findings, dropping
    exact duplicates so a phrase the sweep re-reports isn't double-counted.

    Returns (citations, rule_findings, novel_findings, product_fact_findings).
    """
    seen_c = {(int(c.precedent_index), _normalize_ws(c.current_text)) for c in citations}
    for c in add_citations:
        key = (int(c.precedent_index), _normalize_ws(c.current_text))
        if key not in seen_c:
            seen_c.add(key)
            citations.append(c)

    seen_r = {(int(f.rule_index), _normalize_ws(f.current_text)) for f in rule_findings}
    for f in add_rule_findings:
        key = (int(f.rule_index), _normalize_ws(f.current_text))
        if key not in seen_r:
            seen_r.add(key)
            rule_findings.append(f)

    def _novel_key(f):
        return (_normalize_ws(f.current_text), _normalize_ws(f.reviewer_comment)[:80])

    seen_n = {_novel_key(f) for f in novel_findings}
    for f in add_novel_findings:
        key = _novel_key(f)
        if key not in seen_n:
            seen_n.add(key)
            novel_findings.append(f)

    product_fact_findings = product_fact_findings if product_fact_findings is not None else []
    add_product_fact_findings = add_product_fact_findings or []
    seen_pf = {(int(f.product_index), _normalize_ws(f.current_text), _normalize_ws(f.guardrail_text)[:60])
               for f in product_fact_findings}
    for f in add_product_fact_findings:
        key = (int(f.product_index), _normalize_ws(f.current_text), _normalize_ws(f.guardrail_text)[:60])
        if key not in seen_pf:
            seen_pf.add(key)
            product_fact_findings.append(f)

    return citations, rule_findings, novel_findings, product_fact_findings


def verify_evidence_grounding(
    violations: List[Dict[str, Any]], chunk_text: str
) -> List[Dict[str, Any]]:
    """Critic pass for the precedent path: drop any violation whose cited
    ``current_text`` is not actually present (verbatim, modulo case/whitespace)
    in the chunk being analyzed. This catches the primary LLM fabricating
    evidence — the shape validator does not check substring presence. A
    violation with empty ``current_text`` (e.g. a structural/novel finding)
    is kept. See architect-audit C7.
    """
    norm_chunk = _normalize_ws(chunk_text)
    kept: List[Dict[str, Any]] = []
    for v in violations:
        current = (v.get("current_text") or "").strip()
        if current and _normalize_ws(current) not in norm_chunk:
            logger.info(
                "Critic: dropped violation with fabricated current_text "
                f"(not in chunk): {current[:80]!r}"
            )
            continue
        kept.append(v)
    return kept


# Severity rank (higher = keep) and tier precedence for cross-tier dedupe. A
# human-decided precedent outranks a rule match, which outranks novel judgment.
_SEVERITY_RANK = {
    "critical": 5, "high": 4, "moderate": 3, "medium": 3, "low": 2, "informational": 1,
}
_TIER_RANK = {"disclosure": 5, "product_fact": 4, "precedent": 3, "rule": 2, "novel": 1}


def _violation_rank(v: Dict[str, Any]) -> tuple:
    sev = _SEVERITY_RANK.get(str(v.get("severity", "")).strip().lower(), 0)
    tier = _TIER_RANK.get(((v.get("violation_metadata") or {}).get("grounding") or ""), 0)
    return (sev, tier)


def dedupe_chunk_violations(violations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse violations that quote the SAME phrase within one chunk (the
    three tiers routinely flag the same span — precedent + rule + novel),
    keeping the strongest by severity then tier precedence. Findings with empty
    current_text (structural) are never collapsed. First-occurrence order is
    preserved. See recall fix 2026-06-08 (live run surfaced ~23/60 cross-tier dups)."""
    out: List[Dict[str, Any]] = []
    index_of: Dict[str, int] = {}
    for v in violations:
        key = _normalize_ws(v.get("current_text") or "")
        if not key:
            out.append(v)
            continue
        if key not in index_of:
            index_of[key] = len(out)
            out.append(v)
        elif _violation_rank(v) > _violation_rank(out[index_of[key]]):
            out[index_of[key]] = v
    return out


def mark_structural_findings(
    violations: List[Dict[str, Any]], chunk_text: str = ""
) -> List[Dict[str, Any]]:
    """Route heading / brand-line false-positives to the suppressed review lane
    rather than dropping them. Section-aware chunking isolates short title lines
    (e.g. "Bajaj Life Insurance", "Why Smart Wealth Edge?"), which the LLM then
    over-flags.

    A finding is suppressed ONLY when its current_text is an ENTIRE heading-like
    LINE of the chunk — never a short fragment of a body sentence. This is the
    load-bearing safety property: a real-but-short claim ("Enjoy guaranteed
    tax-free returns…", "UIN: pending") sits inside a longer line, so it is NOT
    a standalone heading and stays scored. Already-suppressed findings keep
    their original reason."""
    from app.services.preprocessing_service import ContextEngineeringService

    heading_lines = {
        _normalize_ws(ln)
        for ln in (chunk_text or "").split("\n")
        if ContextEngineeringService._is_heading_line(ln)
    }
    for v in violations:
        if v.get("suppressed"):
            continue
        ct = _normalize_ws(v.get("current_text") or "")
        if ct and ct in heading_lines:
            v["suppressed"] = True
            v["suppressed_reason"] = "structural heading / brand line (not a claim)"
    return violations


def aggregate_grading(results: List[Dict[str, Any]]) -> tuple:
    """Aggregate per-chunk grading results into (all_violations, failed_count).

    Each result is ``{"violations": [...], "failed": bool}``. A failed chunk
    contributes to the failure count so the engine can refuse to certify a
    document whose analysis did not fully run (fail closed — audit C1).
    """
    violations: List[Dict[str, Any]] = []
    failed = 0
    for r in results:
        if r.get("failed"):
            failed += 1
        violations.extend(r.get("violations") or [])
    return violations, failed


def map_findings_to_violations(
    result: Any,
    precedents: List[Dict[str, Any]],
    *,
    chunk_id,
    chunk_index,
    location: str,
    rules: Optional[List[Dict[str, Any]]] = None,
    product_facts: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Flatten a PrecedentCitationsResult into violation dicts across all four
    grounding tiers: precedent citations, rule-grounded findings, novel
    findings, and product-fact findings. Out-of-range indices and sub-floor
    novel findings are dropped."""
    rules = rules or []
    out: List[Dict[str, Any]] = []
    for c in (result.citations or []):
        idx = int(c.precedent_index)
        if not (0 <= idx < len(precedents)):
            continue
        out.append(
            _citation_to_violation(
                c, precedents[idx], chunk_id=chunk_id, chunk_index=chunk_index, location=location
            )
        )
    for f in (getattr(result, "rule_findings", None) or []):
        idx = int(f.rule_index)
        if not (0 <= idx < len(rules)):
            continue
        out.append(
            _rule_finding_to_violation(
                f, rules[idx], chunk_id=chunk_id, chunk_index=chunk_index, location=location
            )
        )
    for f in (result.novel_findings or []):
        # Always appended — sub-floor findings are persisted as suppressed (for
        # the human review lane), never silently dropped.
        out.append(
            _novel_finding_to_violation(
                f, chunk_id=chunk_id, chunk_index=chunk_index, location=location
            )
        )
    product_facts = product_facts or []
    for f in (getattr(result, "product_fact_findings", None) or []):
        idx = int(f.product_index)
        if not (0 <= idx < len(product_facts)):
            continue
        out.append(
            _product_fact_finding_to_violation(
                f, product_facts[idx], chunk_id=chunk_id, chunk_index=chunk_index, location=location
            )
        )
    return out


@traceable(run_type="chain", name="graph.preprocess_node")
async def preprocess_node(state: ComplianceState) -> Dict:
    """
    Librarian Node: Prepares document content into chunks, then mirrors
    them into the RAG `rag_chunks` index. Indexing failure is non-fatal —
    it degrades downstream retrieval but does not block analysis.
    """
    logger.info("Node: Preprocess (Librarian) running...")

    from app.services.preprocessing_service import ContextEngineeringService
    from app.models.content_chunk import ContentChunk
    from .context import GraphContext

    db = GraphContext.get_db_session()
    submission_id = state["submission_id"]

    service = ContextEngineeringService(db)
    try:
        chunk_count = await service.preprocess_submission(uuid.UUID(submission_id))
        logger.info(f"Preprocessing complete. Chunk count: {chunk_count}")

        # Load chunks into state
        submission_chunks = db.query(ContentChunk).filter(
            ContentChunk.submission_id == submission_id
        ).order_by(ContentChunk.chunk_index).all()

        chunks_data = [
            {
                "id": str(c.id),
                "text": c.text,
                "chunk_index": c.chunk_index,
                "metadata": c.chunk_metadata or {}
            }
            for c in submission_chunks
        ]

        # Mirror chunks into RAG index (non-fatal on failure).
        rag_indexed = 0
        try:
            from app.services.rag.indexers.chunks_indexer import upsert_chunks_for_submission
            rag_indexed = await upsert_chunks_for_submission(
                submission_id=submission_id, db=db, submission_status="analyzing"
            )
        except Exception as e:
            logger.warning(f"RAG chunk indexing failed (non-fatal): {e}")

        # Resolve which approved product(s) this submission is about (additive;
        # a no-match leaves all downstream grounding off). Non-fatal.
        product_match: List[Dict[str, Any]] = []
        try:
            from app.config import settings as _s
            if _s.product_grounding_enabled:
                from app.services.fact_card_service import get_fact_card_service
                from app.services.product_resolver import resolve_products
                full_text = "\n".join(c["text"] for c in chunks_data)
                product_match = resolve_products(
                    full_text, get_fact_card_service(),
                    max_matches=_s.product_match_max,
                    min_fuzzy_score=_s.kb_min_fuzzy_score,
                )
                if product_match:
                    logger.info("product grounding: matched %s",
                                [m["uin"] for m in product_match])
        except Exception as e:
            logger.warning(f"product resolution failed (non-fatal): {e}")

        md = dict(state.get("metadata") or {})
        md["product_match"] = product_match

        return {
            "chunks": chunks_data,
            "metadata": md,
            "messages": [AIMessage(
                content=f"Librarian: Prepared {len(chunks_data)} chunks "
                        f"(RAG indexed: {rag_indexed}; products matched: {len(product_match)})."
            )]
        }

    except Exception as e:
        logger.error(f"Preprocessing failed: {e}")
        return {
            "messages": [AIMessage(content=f"Librarian: Error during preprocessing - {str(e)}")],
            "status": "failed"
        }


async def _resolve_product_grounding(state: Dict, chunks: List[Dict]) -> tuple:
    """Return (product_facts, product_passages) for the matched product(s).

    product_facts: deterministic fact cards (per document). product_passages:
    {chunk_id: [passage,...]} approved-brochure passages, scoped by matched UIN
    (per chunk). Both empty when grounding is off or no product matched.
    Fail-soft: any retrieval error degrades to empty, never raises.
    """
    from app.config import settings
    if not settings.product_grounding_enabled:
        return [], {}
    matches = (state.get("metadata") or {}).get("product_match") or []
    if not matches:
        return [], {}

    uins = [m["uin"] for m in matches]
    try:
        from app.services.fact_card_service import get_fact_card_service
        product_facts = get_fact_card_service().lookup_many(uins)
    except Exception as e:
        logger.warning(f"fact-card lookup failed (non-fatal): {e}")
        product_facts = []

    product_passages: Dict[str, List[Dict]] = {}
    try:
        from app.services.rag.retrievers.product_docs_retriever import get_product_docs_retriever
        retriever = get_product_docs_retriever()
        primary_uin = uins[0]  # scope passages to the first/strongest match
        for c in chunks:
            cid = str(c.get("id"))
            text = c.get("text") or ""
            if not text.strip():
                product_passages[cid] = []
                continue
            try:
                product_passages[cid] = await retriever.retrieve(
                    query=text, uin=primary_uin, top_k=settings.product_docs_top_k,
                )
            except Exception as e:
                logger.warning(f"product-docs retrieval failed for chunk {cid} (non-fatal): {e}")
                product_passages[cid] = []
    # outer guard: retriever-init / non-per-chunk failure -> no passages (fail-soft); per-chunk failures are caught in the loop above
    except Exception as e:
        logger.warning(f"product-docs retrieval failed (non-fatal): {e}")
        product_passages = {}

    return product_facts, product_passages


@traceable(run_type="chain", name="graph.dispatch_node")
async def dispatch_node(state: ComplianceState) -> Dict:
    """
    Brain Node: Identifies active rules and determines execution plan.

    RAG-aware: tries to fetch top-K most-relevant rules per chunk via the
    rules retriever (semantic + keyword hybrid). Falls back to the legacy
    "all active rules per category" path on any RAG failure.
    """
    logger.info("Node: Dispatch (Brain) running...")

    from app.config import settings
    from app.services.rule_generator_service import (
        rule_generator_service,
        RulesUnavailableError,
    )
    from .context import GraphContext

    db = GraphContext.get_db_session()

    # 1. Always load full active rules (fallback target + populates active_agents).
    #    FAIL CLOSED: if the rule store is unreachable we must not grade with zero
    #    rules — mark the run degraded so it routes to needs_review, not "clean".
    try:
        rules_orm = rule_generator_service.get_active_rules(db)
    except RulesUnavailableError as e:
        logger.error(f"dispatch_node: active-rule load failed; degrading run: {e}")
        md = dict(state.get("metadata") or {})
        md["degraded"] = "rules_unavailable"
        return {"metadata": md, "status": "needs_review"}
    rules_serializable: Dict[str, List[Dict]] = {}
    active_agents: List[str] = []
    for cat, r_list in rules_orm.items():
        if r_list:
            rules_serializable[cat] = [
                {
                    "id": str(r.id),
                    "rule_text": r.rule_text,
                    "category": r.category,
                    "severity": r.severity,
                    "keywords": r.keywords or [],
                }
                for r in r_list
            ]
            active_agents.append(f"agent_{cat}")

    active_rule_count = sum(len(v) for v in rules_serializable.values())

    # 2. Try RAG per-chunk retrieval. On any failure, set rag_degraded=true
    #    and let analysis_node use the flat rules_serializable.
    chunks = state.get("chunks", [])
    categories = list(rules_serializable.keys())
    chunk_rules: Dict[str, Dict[str, List[Dict]]] = {}
    rag_degraded = False

    if chunks and categories:
        try:
            from app.services.rag.retrievers.rules_retriever import get_rules_retriever
            retriever = get_rules_retriever()
            chunk_rules = await retriever.retrieve_per_chunk(
                chunks=chunks,
                categories=categories,
                top_k=settings.rag_top_k_analysis,
            )
            retrieved_total = sum(
                len(rs) for chunk_map in chunk_rules.values() for rs in chunk_map.values()
            )
            logger.info(
                f"RAG: retrieved {retrieved_total} rule slots across "
                f"{len(chunk_rules)} chunks × {len(categories)} categories"
            )
            # P1.3 — Enrich each retrieved rule with its regulator source
            # passage (best-effort lookup from rag_source_docs by rule_id).
            # The analysis prompt copies this verbatim into the violation's
            # `regulator_quote` field. Non-fatal on failure.
            try:
                from app.services.rag.retrievers.source_docs_retriever import (
                    get_source_docs_retriever,
                )
                src_retr = get_source_docs_retriever()
                for chunk_map in chunk_rules.values():
                    for rule_list in chunk_map.values():
                        for r in rule_list:
                            rule_id = r.get("id")
                            if not rule_id or r.get("source_quote"):
                                continue
                            try:
                                passages = src_retr.by_rule(rule_id, limit=1)
                                if passages:
                                    r["source_quote"] = (passages[0].get("text") or "")[:300]
                            except Exception:
                                pass
            except Exception as e:
                logger.debug(f"Citation enrichment skipped (non-fatal): {e}")
        except Exception as e:
            logger.warning(f"RAG rule retrieval failed; falling back to all-rules: {e}")
            rag_degraded = True

    md = dict(state.get("metadata") or {})
    md["rag_degraded"] = rag_degraded
    md["rag_rules_per_chunk"] = (
        {cid: sum(len(rs) for rs in cm.values()) for cid, cm in chunk_rules.items()}
        if chunk_rules else {}
    )

    # --- Precedent path (primary analysis driver) ---
    # Guard: if preprocessing produced no chunks (empty document, extraction
    # failure, or DB returned nothing), mark it explicitly as "no_content" so
    # consumers can distinguish "not analyzed" from "clean document".  This is
    # set BEFORE the precedents block so it cannot be overwritten by the
    # "knowledge_base_empty" label below.
    if not chunks:
        md["degraded"] = "no_content"
        logger.warning(
            "dispatch_node: state contains zero chunks — preprocessing produced "
            "no analyzable content. This document will NOT be graded. "
            "Check preprocess_node logs for extraction/chunking errors."
        )

    retrieved_examples: Dict[str, List[Dict]] = {}
    try:
        from app.services.rag.retrievers.precedent_retriever import get_precedent_retriever
        precedent_retriever = get_precedent_retriever()
        retrieved_examples = await precedent_retriever.retrieve_per_chunk(
            chunks=chunks, top_k=settings.pgvector_top_k
        )
    except Exception as e:
        logger.warning(f"Precedent retrieval failed (analysis will find no violations): {e}")
        retrieved_examples = {str(c.get("id")): [] for c in chunks}

    total_precedents = sum(len(v) for v in retrieved_examples.values())
    # Only label "knowledge_base_empty" when chunks exist but the knowledge
    # base returned nothing — the empty-chunks case is already labelled
    # "no_content" above and must not be overwritten with a misleading reason.
    if chunks and total_precedents == 0:
        md["degraded"] = "knowledge_base_empty"
        logger.warning(
            "Knowledge base returned ZERO precedents across all chunks — "
            "analysis will produce no violations. Ingest the precedent corpus "
            "(scripts.ingest_knowledge_base) to enable grading."
        )
    md["precedents_per_chunk"] = {cid: len(v) for cid, v in retrieved_examples.items()}

    if "agent_precedent" not in active_agents:
        active_agents.append("agent_precedent")

    product_facts, product_passages = await _resolve_product_grounding(state, chunks)

    return {
        "active_rules": rules_serializable,
        "chunk_rules": chunk_rules,
        "retrieved_examples": retrieved_examples,
        "active_agents": active_agents,
        "metadata": md,
        "product_facts": product_facts,
        "product_passages": product_passages,
        "messages": [AIMessage(
            content=(
                f"Brain: Dispatched precedent analysis over {len(chunks)} chunks "
                f"({total_precedents} precedents retrieved). "
                f"Legacy rules loaded: {active_rule_count}."
            )
        )]
    }


@traceable(run_type="chain", name="graph.analysis_node")
async def analysis_node(state: ComplianceState) -> Dict:
    """
    Compliance Specialist Node (precedent path): grades each chunk against its
    retrieved reviewer-decision precedents, in parallel. One LLM call per chunk
    at temperature 0. Output contract (ComplianceAnalysisResult) is unchanged.
    """
    logger.info("Node: Analysis (precedent) running...")

    from app.services.preprocessing_service import ContextEngineeringService, build_document_context
    from app.services.llm_service import llm_service
    from app.services.agents.validators import validate_agent_output
    from app.schemas.compliance_schemas import (
        ComplianceAnalysisResult,
        PrecedentCitationsResult,
    )
    from app.models.agent_execution import AgentExecution
    from app.database import SessionLocal
    from app.services.agents.compliance.critic import critique_violations

    chunks_data = state.get("chunks", [])
    retrieved = state.get("retrieved_examples") or {}
    # Per-chunk rules retrieved in dispatch_node — Tier-2 grounding (Fix A).
    # Shape: {chunk_id: {category: [rule_dict, ...]}}.
    chunk_rules = state.get("chunk_rules") or {}
    # Flat active-rule set + the degraded flag, so the rule tier can fall back
    # to the flat rules when per-chunk RAG retrieval failed (else the tier is
    # silently skipped and the doc is graded on precedent+novel alone).
    active_rules = state.get("active_rules") or {}
    rag_degraded = bool((state.get("metadata") or {}).get("rag_degraded"))
    product_facts = state.get("product_facts") or []
    product_passages_by_chunk = state.get("product_passages") or {}
    submission_id = state.get("submission_id")
    user_id = state.get("user_id")

    def _rules_for_chunk(chunk_id) -> List[Dict]:
        return _select_rules_for_chunk(
            chunk_id, chunk_rules, active_rules, rag_degraded, _MAX_RULES_PER_CHUNK
        )

    new_violations: List[Dict] = []

    CORRECTIVE_SUFFIX = (
        "\n\nYour previous output had invalid fields. precedent_index must be an "
        "integer in [0, K-1] where K is the number of precedents, current_text "
        "must be a non-empty substring of the NEW DOCUMENT SECTION, description "
        "at least 5 characters, and confidence between 0 and 1."
    )

    # Bound concurrent per-chunk LLM grading to avoid connection-pool/rate-limit
    # exhaustion. On Groq's free tier each grading prompt is ~6-10k tokens, so
    # firing many at once bursts past the 30k tokens/min ceiling → 429 → the
    # chunk fails → the run fails closed. settings.grade_concurrency (default 2)
    # keeps the in-flight token volume under the per-minute limit; raise it on a
    # paid tier. See config.py.
    from app.config import settings as _settings
    _grade_semaphore = asyncio.Semaphore(max(1, _settings.grade_concurrency))

    async def grade_chunk(chunk_data: Dict) -> Dict:
        chunk_id = chunk_data.get("id")
        chunk_index = chunk_data.get("chunk_index")
        chunk_text = chunk_data.get("text", "")
        precedents = retrieved.get(str(chunk_id), [])
        rules = _rules_for_chunk(chunk_id)
        passages = product_passages_by_chunk.get(str(chunk_id), [])
        # No early-return on empty precedents: the prompt still emits rule-grounded
        # (Tier-2) and novel (Tier-3) findings so issues outside the precedent
        # corpus are still caught (2026-05-28 reviewer-voice design + Fix A).

        async with _grade_semaphore:
            task_db = None
            execution = None
            kept: List[Dict] = []
            failed = False
            try:
                task_db = SessionLocal()
                context_service = ContextEngineeringService(task_db)
                execution = AgentExecution(
                    agent_type="precedent",
                    session_id=uuid.UUID(submission_id) if submission_id else None,
                    user_id=uuid.UUID(user_id) if user_id else None,
                    status="running",
                    input_data={
                        "chunk_index": chunk_index,
                        "text_preview": chunk_text[:100],
                        "precedents_count": len(precedents),
                        "rules_count": len(rules),
                    },
                )
                task_db.add(execution)
                # Flush to assign the PK, capture it as a plain string, THEN
                # commit. Reading execution.id only *after* commit would hit an
                # expired attribute (expire_on_commit defaults to True), firing a
                # refresh SELECT that opens a transaction and pins a pool
                # connection for the entire slow LLM call below. With Semaphore(8)
                # that pinned up to 8 connections across the network round-trip
                # and exhausted the 15-slot pool (QueuePool timeout → chunk fails
                # → run stalls at "waiting for review"). Capturing the id here
                # keeps the session connection-free during the await.
                task_db.flush()
                exec_id = str(execution.id)
                task_db.commit()

                document_context = None
                if _settings.cross_chunk_context_enabled:
                    document_context = build_document_context(
                        chunks_data, chunk_index, _settings.cross_chunk_context_token_budget
                    )
                prompt = context_service.create_precedent_prompts(
                    chunk_text, precedents, rules=rules, document_context=document_context,
                    product_facts=product_facts, product_passages=passages,
                )
                system_prompt = (
                    "You are a senior Bajaj Life Insurance compliance reviewer. Cite "
                    "the historical precedents that apply to the new section. "
                    "Return ONLY valid JSON matching the required schema."
                )

                async def _call(p: str) -> PrecedentCitationsResult:
                    return await llm_service.generate_structured_response(
                        prompt=p,
                        output_model=PrecedentCitationsResult,
                        system_prompt=system_prompt,
                        execution_id=exec_id,
                        db=task_db,
                        tool_name="precedent_citation",
                        temperature=0.0,
                    )

                result = await _call(prompt)
                citations = list(result.citations or [])
                rule_findings = list(getattr(result, "rule_findings", None) or [])
                novel = list(result.novel_findings or [])
                product_ff = list(getattr(result, "product_fact_findings", None) or [])

                # One corrective retry if any citation has an out-of-range
                # index. Keep whichever pass yields more in-range citations;
                # carry that pass's rule + novel findings too.
                def _in_range(cs):
                    return [c for c in cs if 0 <= int(c.precedent_index) < len(precedents)]

                if citations and len(_in_range(citations)) < len(citations):
                    retry = await _call(prompt + CORRECTIVE_SUFFIX)
                    if len(_in_range(retry.citations or [])) >= len(_in_range(citations)):
                        citations = list(retry.citations or [])
                        rule_findings = list(getattr(retry, "rule_findings", None) or [])
                        novel = list(retry.novel_findings or [])
                        product_ff = list(getattr(retry, "product_fact_findings", None) or [])
                citations = _in_range(citations)

                # Completeness sweep: a single structured pass under-enumerates on
                # dense copy, so run one more pass that's told what was already
                # flagged and asked for ONLY additional violations, then merge +
                # dedupe. Non-fatal: a failed sweep keeps the first-pass findings.
                if _settings.completeness_sweep_enabled:
                    try:
                        already = [
                            c.current_text for c in citations
                        ] + [
                            f.current_text for f in rule_findings
                        ] + [
                            f.current_text for f in novel
                        ] + [
                            f.current_text for f in product_ff
                        ]
                        sweep_prompt = context_service.create_completeness_sweep_prompt(
                            chunk_text, precedents, rules=rules, already_found=already,
                            document_context=document_context,
                            product_facts=product_facts, product_passages=passages,
                        )
                        sweep = await _call(sweep_prompt)
                        citations, rule_findings, novel, product_ff = merge_findings(
                            citations, rule_findings, novel,
                            _in_range(list(sweep.citations or [])),
                            list(getattr(sweep, "rule_findings", None) or []),
                            list(sweep.novel_findings or []),
                            product_fact_findings=product_ff,
                            add_product_fact_findings=list(getattr(sweep, "product_fact_findings", None) or []),
                        )
                    except Exception as e:
                        logger.warning(
                            f"Completeness sweep failed (chunk {chunk_index}); "
                            f"keeping first-pass findings: {e}"
                        )

                meta_loc = chunk_data.get("metadata", {})
                loc = f"chunk:{chunk_id}"
                if meta_loc.get("page_number"):
                    loc += f":page:{meta_loc['page_number']}"

                # Map all four tiers — precedent citations, rule-grounded
                # findings (rule_id + regulator_quote carried from the rule),
                # novel findings, and product-fact findings — into the shared
                # violation shape.
                filtered = PrecedentCitationsResult(
                    citations=citations, rule_findings=rule_findings,
                    novel_findings=novel, product_fact_findings=product_ff,
                )
                for v in map_findings_to_violations(
                    filtered, precedents, rules=rules, product_facts=product_facts,
                    chunk_id=chunk_id, chunk_index=chunk_index, location=loc,
                ):
                    ok, errs = validate_agent_output(v)
                    if not ok:
                        _log_grade_error(chunk_id, v, errs)
                        continue
                    kept.append(v)

                # Critic: drop violations whose cited evidence isn't in the
                # chunk (fabricated current_text). See architect-audit C7.
                if _settings.critic_enabled:
                    kept = verify_evidence_grounding(kept, chunk_text)

                # LLM critic (dual-model): an independent gpt-5.4-nano reviews
                # every surviving finding (precedent / rule / novel) and may
                # downgrade or drop hallucinated/stylistic ones. Fail-open and
                # never drops a critical. Separate flag from critic_enabled.
                if _settings.llm_critic_enabled and kept:
                    kept = await critique_violations(
                        chunk_text, rules, kept, precedents
                    )

                # Precision (recall fix 2026-06-08): collapse cross-tier
                # duplicates (same phrase flagged by precedent + rule + novel),
                # then route heading / brand-line false-positives to the
                # suppressed review lane (kept out of the score, not dropped).
                kept = dedupe_chunk_violations(kept)
                kept = mark_structural_findings(kept, chunk_text)

                execution.status = "completed"
                execution.output_data = {"violations": kept}
                task_db.commit()
            except Exception as e:
                # A chunk that fails to grade means the analysis did NOT fully
                # run. Mark it failed so the engine can fail closed rather than
                # silently certifying the document on partial results (audit C1).
                failed = True
                logger.error(f"Precedent grading failed (chunk {chunk_index}): {e}")
                if task_db is not None and execution is not None:
                    try:
                        execution.status = "failed"
                        execution.output_data = {"error": str(e)}
                        task_db.commit()
                    except Exception:
                        pass
            finally:
                if task_db is not None:
                    task_db.close()
            return {"violations": kept, "failed": failed}

    tasks = [grade_chunk(c) for c in chunks_data]
    failed_chunks = 0
    if tasks:
        logger.info(f"Running {len(tasks)} per-chunk precedent grading tasks...")
        results = await asyncio.gather(*tasks)
        new_violations, failed_chunks = aggregate_grading(results)

    # Propagate analysis completeness into metadata so the engine's
    # fail-closed guard can refuse to grade a partially-analyzed document.
    md = dict(state.get("metadata") or {})
    md["analysis_failed_chunks"] = failed_chunks
    if failed_chunks and not md.get("degraded"):
        md["degraded"] = "analysis_incomplete"

    return {
        "violations": new_violations,
        "metadata": md,
        "messages": [AIMessage(
            content=(
                f"Analysis: Found {len(new_violations)} violations "
                f"(precedent path); {failed_chunks} chunk(s) failed to grade."
            )
        )]
    }


def _log_grade_error(chunk_id, violation: Dict, errors: List[str]) -> None:
    import os
    import json as _json
    os.makedirs("logs", exist_ok=True)
    with open(os.path.join("logs", "grade_errors.log"), "a", encoding="utf-8") as f:
        f.write(_json.dumps({"chunk_id": str(chunk_id), "errors": errors, "violation": violation}) + "\n")


@traceable(run_type="chain", name="graph.disclosure_node")
async def disclosure_node(state: ComplianceState) -> Dict:
    """Deterministic mandatory-disclosure checker. Runs after analysis so it
    sees the whole document + resolved product. Emits document-level findings
    for missing/altered mandated disclaimers; fails closed if the registry is
    unavailable. See docs/superpowers/specs/2026-06-26-mandatory-disclosure-checker-design.md."""
    from app.config import settings as _s
    if not _s.disclosure_check_enabled:
        return {}

    from app.services.disclaimer.registry import get_disclaimer_registry
    from app.services.disclaimer.triggers import derive_product_context, resolve_required
    from app.services.disclaimer.matcher import classify
    from app.services.fact_card_service import get_fact_card_service

    md = dict(state.get("metadata") or {})
    registry = get_disclaimer_registry()
    if not registry.loaded_ok:
        logger.error("disclosure_node: registry unavailable — failing closed")
        md["degraded"] = "disclosure_unavailable"
        return {"metadata": md}

    document_text = "\n".join((c.get("text") or "") for c in (state.get("chunks") or []))
    if not document_text.strip():
        return {}

    ctx = derive_product_context(md.get("product_match") or [], get_fact_card_service())
    required, recall_degraded = await resolve_required(
        document_text, ctx, registry,
        llm_call=_disclosure_llm_call,
        enable_llm=_s.disclosure_llm_backstop_enabled,
    )

    violations: List[Dict[str, Any]] = []
    summary: List[Dict[str, Any]] = []
    for did, info in required.items():
        d = registry.get(did)
        if d is None:
            continue
        status, sim = classify(d.text, d.anchors, document_text, d.present_threshold, d.altered_threshold)
        summary.append({"disclaimer_id": did, "status": status, "similarity": round(sim, 3),
                        "provenance": info["provenance"], "source": info["source"]})
        if status == "present":
            continue
        confidence = 1.0 if info["source"] == "deterministic" else 0.85
        violations.append(_disclosure_finding_to_violation(
            d, status=status, similarity=sim, provenance=info["provenance"], confidence=confidence))

    if recall_degraded:
        md["disclosure_recall_degraded"] = True

    logger.info("disclosure_node: %d required, %d findings", len(required), len(violations))
    return {
        "violations": violations,
        "disclosure_findings": list(violations),
        "required_disclosures": summary,
        "metadata": md,
    }


@traceable(run_type="chain", name="graph.scoring_node")
async def scoring_node(state: ComplianceState) -> Dict:
    """
    Scoring Node: Calculates final compliance grades.
    """
    logger.info("Node: Scoring running...")

    from app.services.agents.compliance.scoring import scoring_service
    from .context import GraphContext

    db = GraphContext.get_db_session()
    violations = state.get("violations", [])
    active_rules = state.get("active_rules", {})
    categories = list(active_rules.keys())

    scores = scoring_service.calculate_scores(violations, db=db, categories=categories)

    return {
        "scores": scores,
        "messages": [AIMessage(content=f"Scoring: Grade {scores.get('grade')} ({scores.get('overall')}%)")]
    }


async def refinement_node(state: ComplianceState) -> Dict:
    """
    Refinement Node: HITL Review point.
    Graph is set to interrupt_before this node for human review.
    """
    logger.info("Node: Refinement (HITL) running...")

    feedback = state.get("user_feedback")
    if feedback:
        return {
            "messages": [AIMessage(content=f"Refinement: Processed human feedback: {feedback}")]
        }

    return {"messages": [AIMessage(content="Refinement: No feedback, proceeding to completion.")]}
