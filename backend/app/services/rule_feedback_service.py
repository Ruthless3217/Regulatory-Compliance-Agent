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

from app.models.rule import Rule
from app.models.rule_feedback import RuleFeedback
from app.models.violation import Violation
from app.services.agents.compliance.reliability import apply_verdict, theta

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
        else:
            db.add(
                RuleFeedback(
                    violation_id=violation.id,
                    rule_id=violation.rule_id,  # denormalized: survives rule unlink
                    verdict=verdict,
                    severity_override=severity_override,
                    reviewer_id=reviewer_id,
                    comment=comment,
                )
            )

        # --- bounded weight update on the responsible rule ---
        weight_updated = False
        reliability: Optional[float] = None
        rule = None
        if violation.rule_id is not None:
            rule = db.query(Rule).filter(Rule.id == violation.rule_id).first()
        if rule is not None:
            alpha, beta = apply_verdict(
                rule.reliability_alpha,
                rule.reliability_beta,
                verdict,
                previous_verdict=previous_verdict,
            )
            rule.reliability_alpha = alpha
            rule.reliability_beta = beta
            reliability = theta(alpha, beta)
            weight_updated = True
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


rule_feedback_service = RuleFeedbackService()
