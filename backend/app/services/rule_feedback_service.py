"""Rule Feedback Service — the HITL write-path for adaptive rule weights.

A reviewer's accept/reject verdict on one finding is the ONLY signal that
moves a rule's Beta pseudo-counts (reliability_alpha/beta). The document-level
reviewer score is deliberately not handled here: it is held-out evaluation
data (see ComplianceCheck.reviewer_score) and training on it would Goodhart
the metric that proves the system improves.
"""
import logging
import uuid
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models.rule import Rule
from app.models.rule_feedback import RuleFeedback
from app.models.violation import Violation
from app.services.agents.compliance.reliability import apply_verdict, theta

logger = logging.getLogger(__name__)

_ALLOWED_SEVERITIES = {"critical", "high", "medium", "low", "moderate", "informational"}


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


rule_feedback_service = RuleFeedbackService()
