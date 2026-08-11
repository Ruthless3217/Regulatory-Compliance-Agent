"""Rule Feedback Service — the HITL write-path for adaptive rule weights.

A reviewer's accept/reject verdict on one finding is the ONLY signal that
moves a rule's Beta pseudo-counts (reliability_alpha/beta). The document-level
reviewer score is deliberately not handled here: it is held-out evaluation
data (see ComplianceCheck.reviewer_score) and training on it would Goodhart
the metric that proves the system improves.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models.corpus_layer import CorpusLayer
from app.models.rule import Rule
from app.models.rule_feedback import RuleFeedback
from app.models.rule_reliability_event import RuleReliabilityEvent
from app.models.violation import Violation
from app.services import corpus_layer_service
from app.services.agents.compliance.reliability import apply_verdict, theta
from app.services.precedent.dedup import precedent_id

logger = logging.getLogger(__name__)

_ALLOWED_SEVERITIES = {"critical", "high", "medium", "low", "moderate", "informational"}

# The reviewer-action taxonomy (0023) maps onto apply_feedback's older
# accept/reject vocabulary for weight math only; 'dismiss' has no equivalent
# because it never touches rule weights.
_ACTION_TO_LEGACY_VERDICT = {"correct": "accept", "not_violation": "reject"}
_LEGACY_VERDICTS = ("accept", "reject")


class RuleFeedbackService:
    """Applies reviewer verdicts: one audit row + one bounded weight update."""

    @staticmethod
    def apply_feedback(
        db: Session,
        violation_id: str,
        verdict: str,
        reviewer_id: Optional[uuid.UUID] = None,
        severity_override: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> Dict:
        """Record a verdict on a finding and update the rule's reliability.

        Upserts per (violation, reviewer): a flipped verdict reverts the old
        pseudo-count before applying the new one (net effect: one verdict).
        Findings without a rule (precedent/novel tier) record the verdict for
        evaluation but update no weights.

        Raises ValueError on unknown violation, bad verdict, or bad severity —
        before anything is written.
        """
        # --- validate everything before touching state ---
        if verdict not in ("accept", "reject"):
            raise ValueError(f"verdict must be 'accept' or 'reject', got {verdict!r}")
        if severity_override is not None and severity_override not in _ALLOWED_SEVERITIES:
            raise ValueError(
                f"severity_override must be one of {sorted(_ALLOWED_SEVERITIES)}, "
                f"got {severity_override!r}"
            )

        violation = db.query(Violation).filter(Violation.id == violation_id).first()
        if violation is None:
            raise ValueError(f"Violation {violation_id} not found")

        existing = (
            db.query(RuleFeedback)
            .filter(
                RuleFeedback.violation_id == violation.id,
                RuleFeedback.reviewer_id == reviewer_id,
            )
            .first()
        )
        previous_verdict = existing.verdict if existing is not None else None

        # --- upsert the audit row ---
        if existing is not None:
            existing.verdict = verdict
            existing.severity_override = severity_override
            existing.comment = comment
            feedback_row = existing
        else:
            feedback_row = RuleFeedback(
                violation_id=violation.id,
                rule_id=violation.rule_id,  # denormalized: survives rule unlink
                verdict=verdict,
                severity_override=severity_override,
                reviewer_id=reviewer_id,
                comment=comment,
            )
            db.add(feedback_row)
            # Flush so the new row gets its PK, which the reliability event
            # below links to. Same transaction — the commit is still one unit.
            db.flush()

        # --- bounded weight update on the responsible rule ---
        weight_updated = False
        reliability: Optional[float] = None
        rule = None
        if violation.rule_id is not None:
            rule = db.query(Rule).filter(Rule.id == violation.rule_id).first()
        if rule is not None:
            # Snapshot BEFORE mutating — `rules.reliability_alpha/beta` is
            # updated in place, so this is the only moment the prior value
            # exists anywhere.
            alpha_before = rule.reliability_alpha
            beta_before = rule.reliability_beta
            theta_before = theta(alpha_before, beta_before)

            alpha, beta = apply_verdict(
                alpha_before,
                beta_before,
                verdict,
                previous_verdict=previous_verdict,
            )
            rule.reliability_alpha = alpha
            rule.reliability_beta = beta
            reliability = theta(alpha, beta)
            weight_updated = True

            # Append-only history (migration 0029): without this row the
            # before/after pair is unrecoverable and the Model-learning
            # reliability panel can only report insufficient_data.
            db.add(
                RuleReliabilityEvent(
                    rule_id=rule.id,
                    rule_feedback_id=feedback_row.id,
                    alpha_before=alpha_before,
                    beta_before=beta_before,
                    alpha_after=alpha,
                    beta_after=beta,
                    theta_before=theta_before,
                    theta_after=reliability,
                )
            )
            logger.info(
                f"Rule {rule.id} reliability updated by verdict '{verdict}' "
                f"(prev={previous_verdict}): α={alpha} β={beta} θ={reliability:.3f}"
            )

        db.commit()

        return {
            "violation_id": str(violation.id),
            "rule_id": str(violation.rule_id) if violation.rule_id else None,
            "verdict": verdict,
            "weight_updated": weight_updated,
            "reliability": reliability,
        }

    @staticmethod
    def apply_action(
        db: Session,
        violation_id: str,
        action: str,
        reviewer_id: Optional[uuid.UUID] = None,
        reason: Optional[str] = None,
        explanation: Optional[str] = None,
        final_text: Optional[str] = None,
        severity_override: Optional[str] = None,
        routed_queue: Optional[str] = None,
    ) -> Dict:
        """Reviewer-action taxonomy (correct/not_violation/dismiss) — the real
        third-button flow replacing the binary accept/reject shim above.

        correct/not_violation delegate weight math to `apply_feedback`
        UNCHANGED (mapped to its accept/reject vocabulary); dismiss skips it
        entirely. Either way, the RuleFeedback row's `verdict` column ends up
        holding the real taxonomy value (0023 widened it for exactly this),
        plus the reviewer-action snapshot columns.

        Raises ValueError on unknown violation/action/severity, same contract
        as apply_feedback.
        """
        if action not in ("correct", "not_violation", "dismiss"):
            raise ValueError(
                f"action must be one of 'correct', 'not_violation', 'dismiss', got {action!r}"
            )
        if severity_override is not None and severity_override not in _ALLOWED_SEVERITIES:
            raise ValueError(
                f"severity_override must be one of {sorted(_ALLOWED_SEVERITIES)}, "
                f"got {severity_override!r}"
            )

        violation = db.query(Violation).filter(Violation.id == violation_id).first()
        if violation is None:
            raise ValueError(f"Violation {violation_id} not found")

        weight_updated = False
        reliability: Optional[float] = None

        # ponytail: known ceiling — dismissing a violation that was previously
        # 'correct'/'not_violation' does NOT revert that earlier weight (only
        # a later correct/not_violation resubmission reverts it, via the
        # normalization below). A correct -> dismiss -> not_violation sequence
        # on the same (violation, reviewer) therefore leaves the original
        # 'correct' pseudo-count unreverted. Upgrade path if this matters:
        # revert-on-dismiss too, symmetric with the normalization below.
        if action in _ACTION_TO_LEGACY_VERDICT:
            # apply_feedback's own revert math only understands accept/reject
            # previous verdicts. A prior /actions call on this exact
            # (violation, reviewer) may have left a richer taxonomy value
            # ('correct'/'not_violation'/'dismiss') on the row — normalize it
            # to whatever was ACTUALLY applied to alpha/beta before
            # delegating, so a reviewer changing their mind reverts the right
            # pseudo-count instead of apply_feedback raising or double-
            # counting. A prior 'dismiss' applied no weight, so drop that row
            # entirely (fresh application, nothing to revert).
            existing = (
                db.query(RuleFeedback)
                .filter(
                    RuleFeedback.violation_id == violation.id,
                    RuleFeedback.reviewer_id == reviewer_id,
                )
                .first()
            )
            if existing is not None and existing.verdict not in _LEGACY_VERDICTS:
                if existing.verdict == "dismiss":
                    db.delete(existing)
                    db.flush()
                else:
                    existing.verdict = _ACTION_TO_LEGACY_VERDICT.get(
                        existing.verdict, existing.verdict
                    )

            legacy_verdict = _ACTION_TO_LEGACY_VERDICT[action]
            result = RuleFeedbackService.apply_feedback(
                db, violation_id, legacy_verdict,
                reviewer_id=reviewer_id, severity_override=severity_override,
                comment=explanation,
            )
            weight_updated = result["weight_updated"]
            reliability = result["reliability"]

        # Upsert the row again to stamp the REAL taxonomy value + the
        # reviewer-action snapshot columns (apply_feedback above only wrote
        # 'accept'/'reject' as a means to correct weight math).
        fb = (
            db.query(RuleFeedback)
            .filter(
                RuleFeedback.violation_id == violation.id,
                RuleFeedback.reviewer_id == reviewer_id,
            )
            .first()
        )
        if fb is None:
            fb = RuleFeedback(
                violation_id=violation.id,
                reviewer_id=reviewer_id,
                rule_id=violation.rule_id,
                verdict=action,
            )
            db.add(fb)

        fb.verdict = action
        fb.reason = reason
        fb.comment = explanation
        fb.severity_override = severity_override
        fb.final_text = final_text
        fb.original_text = violation.current_text
        fb.suggested_text = violation.suggested_fix
        fb.confidence_snapshot = violation.confidence
        fb.rule_id = violation.rule_id
        if violation.compliance_check is not None:
            fb.submission_id = violation.compliance_check.submission_id
        fb.analysis_run_id = violation.analysis_run_id
        run = violation.analysis_run
        if run is not None and run.run_metadata:
            fb.retrieval_snapshot = run.run_metadata.get("retrieval_debug")
            fb.product_snapshot = run.run_metadata.get("product_match")
        from app.config import settings
        fb.model_version = settings.llm_model
        # No dedicated KB/index-version field exists yet — the embedding
        # model name is the closest proxy for "which KB build produced this".
        # ponytail: swap for a real KB build id if/when one exists.
        fb.kb_version = settings.rag_embedding_model
        fb.routed_queue = routed_queue

        violation.review_status = "actioned"
        violation.resolved_at = datetime.now(timezone.utc)
        db.add(violation)

        db.commit()

        return {
            "violation_id": str(violation.id),
            "rule_id": str(violation.rule_id) if violation.rule_id else None,
            "action": action,
            "weight_updated": weight_updated,
            "reliability": reliability,
            "routed_queue": routed_queue,
            "review_status": violation.review_status,
            "resolved_at": violation.resolved_at.isoformat() if violation.resolved_at else None,
        }


# --------------------------------------------------------------------------
# Reviewer feedback as a REMOVABLE LAYER over the precedents
# --------------------------------------------------------------------------
#
# A verdict is more than a weight nudge: "this wording is wrong / this flag was
# wrong" is exactly the shape of a precedent. Projecting it into
# `precedent_cases` under one dedicated corpus layer means the next analysis
# retrieves reviewer judgments identically to ingested ones — and an admin can
# take the entire contribution back out with a single boolean, because
# pgvector_store._LAYER_GUARD hides every precedent of a disabled layer from
# retrieval instantly, with nothing re-embedded and nothing deleted.

REVIEWER_FEEDBACK_LAYER_NAME = "Reviewer feedback"
REVIEWER_FEEDBACK_SOURCE_FILE = "reviewer-feedback"
_REVIEWER_FEEDBACK_LAYER_DESCRIPTION = (
    "Precedents taught by reviewer verdicts on live findings (Correct / "
    "Not-a-violation). Disable to remove every reviewer-taught precedent from "
    "retrieval without losing the feedback itself."
)


def _reviewer_canonical_hash(violation_id) -> str:
    """`precedent_cases.canonical_hash` is NOT NULL UNIQUE. Keying it on the
    violation rather than on the content keeps it unique when two reviewers
    write the same sentence about different findings, and STABLE when one
    reviewer edits their own comment."""
    return f"reviewer_feedback:{violation_id}"


def _reviewer_precedent_id(violation_id) -> str:
    """One violation, one precedent row, forever — a changed verdict or an
    edited comment UPDATEs it (ON CONFLICT (id)) instead of piling up a new
    precedent per click."""
    return precedent_id(_reviewer_canonical_hash(violation_id))


def _reviewer_layer_id(db: Session, *, create: bool):
    """The singleton layer, created on first use. Returns None when it does not
    exist and `create` is False (a dismiss has nothing to create it for)."""
    layer = (
        db.query(CorpusLayer)
        .filter(CorpusLayer.name == REVIEWER_FEEDBACK_LAYER_NAME)
        .first()
    )
    if layer is not None:
        return layer.id
    if not create:
        return None
    # ponytail: two concurrent first-ever verdicts can race the unique name
    # constraint; the loser is logged by the fail-soft wrapper and its
    # precedent skipped. Upgrade path: ON CONFLICT (name) DO NOTHING + re-read.
    created = corpus_layer_service.create_layer(
        db,
        name=REVIEWER_FEEDBACK_LAYER_NAME,
        kind="reviewer_feedback",
        description=_REVIEWER_FEEDBACK_LAYER_DESCRIPTION,
        claim=False,  # nothing pre-existing belongs to this layer
    )
    return uuid.UUID(created["id"])


def _reviewer_precedent_row(
    violation: Violation,
    action: str,
    *,
    reason: Optional[str],
    explanation: Optional[str],
    final_text: Optional[str],
    product_category: Optional[str],
    submission_id=None,
) -> Dict:
    """Map one actioned finding onto the ingest row schema.

    `precedent_cases` has no polarity column — every ingested row IS a
    violation — so a `not_violation` verdict has to carry its direction in the
    text that reaches the prompt: `issue_type` ("Violation type:" in the
    precedent block) and `why_rationale` ("Why it was flagged:"). Both are also
    part of the embedded signature, so the polarity is in the vector too.
    """
    category = (violation.category or "General Compliance").strip()
    note = (explanation or "").strip()
    described = (violation.description or "").strip()
    reason_txt = f" (reason: {reason})" if reason else ""

    if action == "not_violation":
        issue_type = f"Not a violation — {category}"
        why = (
            "A compliance reviewer read this exact finding on a live submission "
            f"and judged it NOT a violation{reason_txt}. Wording like the flagged "
            "phrase below is acceptable in this context; do not raise it."
        )
        severity = "informational"
        # The rejected machine suggestion is NOT an approved rewrite. Only text
        # the reviewer actually typed counts as one.
        after_text = final_text
    else:
        issue_type = category
        why = note or described or f"A compliance reviewer confirmed this {category} finding."
        severity = violation.severity or "moderate"
        after_text = final_text or violation.suggested_fix

    comment = note or why
    span = (
        (violation.current_text or "").strip()
        or (violation.cited_anchor_text or "").strip()
        or described
    )
    return {
        "id": _reviewer_precedent_id(violation.id),
        "canonical_hash": _reviewer_canonical_hash(violation.id),
        "highlighted_span": span,
        "span_context": described or None,
        "reviewer_comment": comment,
        "reviewer_role": "reviewer",
        "is_reviewer": True,
        "thread": [],
        "resolved": True,
        "before_text": violation.current_text,
        "after_text": after_text,
        "regulation_tags": [category],
        "issue_type": issue_type,
        "why_rationale": why,
        "guideline_ref": violation.cited_section,
        "severity": severity,
        "product_category": product_category,
        # `ticket` is what the retriever exposes as `document_id`, which the
        # eval harness uses for its same-document leakage guard.
        "ticket": str(submission_id) if submission_id else None,
        "source_file": REVIEWER_FEEDBACK_SOURCE_FILE,
        "comment_date": datetime.now(timezone.utc).isoformat(),
        "occurrence_count": 1,
        "example_tickets": [str(submission_id)] if submission_id else [],
    }


async def sync_reviewer_precedent(
    db: Session,
    violation_id,
    action: str,
    *,
    reason: Optional[str] = None,
    explanation: Optional[str] = None,
    final_text: Optional[str] = None,
) -> Optional[str]:
    """Project one reviewer verdict into the "Reviewer feedback" corpus layer.

    correct/not_violation upsert THE precedent for this violation; dismiss
    removes it, because a dismissed finding teaches nothing and an inert row
    left behind is a row someone has to reason about later.

    Fail-soft by contract: `apply_action` has already committed the
    RuleFeedback row before this runs, and capturing reviewer feedback must
    never depend on the embedder or the vector store being reachable. Every
    failure is logged and swallowed; the caller's result is unaffected.
    """
    try:
        violation = db.query(Violation).filter(Violation.id == violation_id).first()
        if violation is None:
            return None

        if action not in _ACTION_TO_LEGACY_VERDICT:  # dismiss (or anything else)
            layer_id = _reviewer_layer_id(db, create=False)
            if layer_id is None:
                return None
            try:
                corpus_layer_service.delete_item(
                    db, layer_id, _reviewer_precedent_id(violation.id)
                )
                logger.info(
                    "reviewer precedent removed for violation %s (action=%s)",
                    violation_id, action,
                )
            except LookupError:
                pass  # this finding never taught anything
            return None

        check = violation.compliance_check
        submission = getattr(check, "submission", None) if check is not None else None
        row = _reviewer_precedent_row(
            violation,
            action,
            reason=reason,
            explanation=explanation,
            final_text=final_text,
            product_category=getattr(submission, "product_line", None),
            submission_id=getattr(check, "submission_id", None) if check is not None else None,
        )
        layer_id = _reviewer_layer_id(db, create=True)
        await corpus_layer_service.add_precedents(db, layer_id, [row])
        logger.info(
            "reviewer precedent upserted: violation=%s action=%s precedent=%s layer=%s",
            violation_id, action, row["id"], layer_id,
        )
        return row["id"]
    except Exception:
        logger.exception(
            "reviewer precedent sync failed for violation %s (action=%s) — the "
            "reviewer's feedback was still recorded",
            violation_id, action,
        )
        try:
            db.rollback()
        except Exception:  # pragma: no cover - a dead session is already logged
            pass
        return None


rule_feedback_service = RuleFeedbackService()
