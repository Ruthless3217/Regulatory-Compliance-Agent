"""Single source of truth for violation JSON shape.

Replaces the two hand-duplicated serializers (`compliance.py`'s
`_serialize_violation` and `engine.py`'s `get_check_summary` dict) that had
drifted apart and silently dropped already-populated columns
(`cited_section`, `cited_page`, `cited_regulation_version`, `rule_version`).

`serialize_violation` issues no query of its own for the reviewer `feedback`
row, so callers control how it's fetched — pass the result of
`latest_feedback_map` for one bulk query instead of one query per violation.
Its one relationship access (`v.creator`, for the author badge on
reviewer-authored flags) is a no-op whenever `created_by` is NULL, which is
every model-authored row.
"""
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.models.violation import Violation
from app.models.rule_feedback import RuleFeedback


def finding_counts(violations: List) -> Dict[str, int]:
    """Return explicit scored/suppressed/total finding counts.

    Suppressed rows are intentionally persisted for audit and human review but
    do not affect the score. Returning one unlabeled violation_count made
    different screens disagree depending on whether their query included that
    review lane, so every consumer now derives all three counts together.
    """
    total = len(violations)
    suppressed = sum(
        1
        for violation in violations
        if bool(
            violation.get("suppressed")
            if isinstance(violation, dict)
            else getattr(violation, "suppressed", False)
        )
    )
    return {
        "scored": total - suppressed,
        "suppressed": suppressed,
        "total": total,
    }


def serialize_violation(v: Violation, feedback: Optional[RuleFeedback] = None) -> dict:
    return {
        "id": str(v.id),
        "category": v.category,
        "severity": v.severity,
        "description": v.description,
        "location": v.location,
        "current_text": v.current_text,
        "suggested_fix": v.suggested_fix,
        "auto_fixable": v.auto_fixable,
        "chunk_index": v.chunk_index,
        "rule_id": str(v.rule_id) if v.rule_id else None,
        "confidence": v.confidence,
        "regulator_quote": v.regulator_quote,
        # Reviewer-voice tags (2026-05-28): action_type, evidence_needed,
        # grounding (precedent|novel), regulatory_basis. Live in JSONB; the UI
        # renders them as the action/needed/source badge row.
        "violation_metadata": v.violation_metadata,
        # Precedent-citation provenance (Phase 1.5). All fields are nullable;
        # populated only when the violation came from the precedent path.
        "cited_precedent_id": str(v.cited_precedent_id) if v.cited_precedent_id else None,
        "cited_document_id": v.cited_document_id,
        "cited_source_file": v.cited_source_file,
        "cited_anchor_text": v.cited_anchor_text,
        "cited_comment_verbatim": v.cited_comment_verbatim,
        "cited_final_text": v.cited_final_text,
        "similarity_score": v.similarity_score,
        # Rule-citation locators (rule path) — previously dropped by both
        # hand-rolled serializers despite being populated at write time.
        "cited_section": v.cited_section,
        "cited_page": v.cited_page,
        "cited_regulation_version": v.cited_regulation_version,
        "rule_version": v.rule_version,
        # Sub-confidence-floor / structural findings: persisted but kept out of
        # the score and surfaced in a separate "Needs review" lane in the UI.
        "suppressed": bool(v.suppressed),
        "suppressed_reason": v.suppressed_reason,
        # 0023 — which AnalysisRun produced this finding, and its
        # reviewer-facing lifecycle (open/actioned).
        "analysis_run_id": str(v.analysis_run_id) if v.analysis_run_id else None,
        "review_status": v.review_status,
        "resolved_at": v.resolved_at.isoformat() if v.resolved_at else None,
        # 0024 — real page/bbox anchor for the document viewer.
        "section_title": v.section_title,
        "anchor_page": v.anchor_page,
        "anchor_bbox": v.anchor_bbox,
        # 0028 — has the suggested fix already been written into the document.
        "fix_applied": bool(v.fix_applied),
        "fix_applied_at": v.fix_applied_at.isoformat() if v.fix_applied_at else None,
        # 0031 — authorship: 'model' (an analysis run produced it) or
        # 'reviewer' (a human flagged text the model missed). The UI badges the
        # two differently and only a reviewer-authored flag is deletable.
        "source": v.source or "model",
        "created_by": str(v.created_by) if v.created_by else None,
        # Author's display name for the "added by X" badge. No query for
        # model-authored rows (created_by is NULL, so the relationship never
        # loads) — one for each reviewer-authored row, which are rare.
        "created_by_username": v.creator.username if v.creator else None,
        # Left-joined latest reviewer verdict (rule_feedback), if any.
        "reviewer_verdict": feedback.verdict if feedback else None,
        "reviewer_comment": feedback.comment if feedback else None,
    }


def latest_feedback_map(db: Session, violation_ids: List) -> Dict[str, RuleFeedback]:
    """violation_id (str) -> most-recently-updated RuleFeedback row.

    Lets a violation list be serialized with reviewer_verdict/reviewer_comment
    via one bulk query instead of one query per violation. A violation can
    carry feedback from more than one reviewer (unique per violation+reviewer);
    "latest by updated_at" is the one shown, matching the single-verdict-card
    UI — not a multi-reviewer aggregation.
    """
    ids = [str(vid) for vid in violation_ids if vid is not None]
    if not ids:
        return {}
    rows = (
        db.query(RuleFeedback)
        .filter(RuleFeedback.violation_id.in_(ids))
        .order_by(RuleFeedback.updated_at.desc())
        .all()
    )
    out: Dict[str, RuleFeedback] = {}
    for r in rows:
        key = str(r.violation_id)
        if key not in out:
            out[key] = r
    return out
