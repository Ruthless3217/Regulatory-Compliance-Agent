from typing import List, Dict, Optional
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)

try:
    from langsmith import traceable
except Exception:  # pragma: no cover
    def traceable(*_a, **_kw):  # type: ignore
        def _d(fn): return fn
        return _d if not (_a and callable(_a[0])) else _a[0]


class ScoringService:
    """Calculate compliance scores based on violations."""

    SEVERITY_WEIGHTS = {
        "critical": 20,
        "high": 10,
        "medium": 5,
        "low": 2,
        "moderate": 8,        # added — new precedent vocab
        "informational": 2,   # added — new precedent vocab
    }

    # A critical finding at or above this confidence forces a failing verdict and
    # caps the grade at C, regardless of how few/many other findings exist. Below
    # it, a critical is uncertain → "flagged" (human review) rather than auto-fail.
    CRITICAL_CONFIDENCE_THRESHOLD = 0.50
    # Score ceiling applied when a high-confidence critical is present: keeps the
    # grade at C or below (grade C spans 70–79; 70 guarantees ≤ C).
    CRITICAL_SCORE_CAP = 70.0

    @staticmethod
    @traceable(run_type="tool", name="Scoring.calculate_scores")
    def calculate_scores(
        violations: List[Dict],
        db: Optional[Session] = None,
        project_config: Optional[Dict] = None,
        categories: Optional[List[str]] = None
    ) -> Dict[str, float]:
        """
        Absolute-deduction compliance score.

        overall = 100 − Σ (severity_weight(v) × confidence(v)), clamped to [0,100],
        plus a hard cap: any critical finding at/above CRITICAL_CONFIDENCE_THRESHOLD
        caps the grade at C and fails the verdict.

        This is MONOTONIC in severity: adding a finding (or raising a finding's
        severity/confidence) can only lower the score. The previous model weighted
        each *discovered category* equally (1/N), so spreading violations across
        more categories diluted every category's impact — a document with one
        critical could out-score a document with five mixed findings. Category
        count must not change the grade; only severity × confidence does.

        `categories`/`project_config` are accepted for back-compat but no longer
        re-weight the overall score (which is now category-count-independent).
        Per-category sub-scores are still returned for the UI, computed the same
        absolute way within each category.
        """
        # Suppressed (sub-confidence-floor / uncertain) findings are persisted for
        # human review but MUST NOT move the score — they're not certain enough to
        # penalize. They still appear in the review lane.
        scored_violations = [v for v in violations if not v.get("suppressed")]

        enriched_violations = ScoringService._enrich_violations_with_points(scored_violations, db)

        # Overall = absolute deductions from 100, independent of category count.
        total_deduction = sum(v.get("points_deduction", 0) for v in enriched_violations)
        overall_score = max(0.0, min(100.0, 100.0 - total_deduction))

        # Hard critical cap (confidence-aware): a credible critical can never grade
        # better than C, even if it's the only finding.
        high_conf_critical = any(
            v.get("severity") == "critical"
            and ScoringService._confidence_weight(v) >= ScoringService.CRITICAL_CONFIDENCE_THRESHOLD
            for v in enriched_violations
        )
        if high_conf_critical:
            overall_score = min(overall_score, ScoringService.CRITICAL_SCORE_CAP)

        # Per-category sub-scores (display only) — same absolute model per category.
        discovered_categories = set()
        for v in enriched_violations:
            for c in str(v.get("category", "general")).split("|"):
                discovered_categories.add(c.strip())
        category_scores = {
            cat: ScoringService._calculate_category_score(enriched_violations, cat)
            for cat in discovered_categories
        }

        grade = ScoringService._get_grade(overall_score)
        status = ScoringService._get_status(enriched_violations, overall_score)

        result = {
            "overall": round(overall_score, 2),
            "grade": grade,
            "status": status,
        }
        for cat, score in category_scores.items():
            result[cat] = round(score, 2)
        return result

    @staticmethod
    def _confidence_weight(violation: Dict) -> float:
        """Confidence in [0,1] used to scale a violation's penalty. Missing /
        invalid confidence → 1.0 (full penalty): for a compliance tool we must
        never silently under-penalize a violation (audit H16)."""
        conf = violation.get("confidence")
        try:
            conf = float(conf) if conf is not None else 1.0
        except (TypeError, ValueError):
            conf = 1.0
        return max(0.0, min(1.0, conf))

    @staticmethod
    def _enrich_violations_with_points(
        violations: List[Dict],
        db: Optional[Session] = None
    ) -> List[Dict]:
        if not db:
            for violation in violations:
                severity = violation.get("severity", "low")
                base = ScoringService.SEVERITY_WEIGHTS.get(severity, 5)
                violation["points_deduction"] = base * ScoringService._confidence_weight(violation)
            return violations

        try:
            from app.models.rule import Rule
        except ImportError:
            from ....models.rule import Rule

        import uuid

        from app.services.agents.compliance.reliability import theta

        enriched = []
        for violation in violations:
            rule_id = violation.get("rule_id")
            points_deduction = None
            rule = None

            if rule_id:
                try:
                    uuid.UUID(str(rule_id))
                    rule = db.query(Rule).filter(Rule.id == rule_id).first()
                    if rule and rule.points_deduction:
                        points_deduction = abs(float(rule.points_deduction))
                except Exception:
                    pass

            if points_deduction is None:
                severity = violation.get("severity", "low")
                points_deduction = ScoringService.SEVERITY_WEIGHTS.get(severity, 5)

            # Adaptive rule weights: scale by the rule's learned reliability
            # θ = α/(α+β) (reviewer accept/reject history, Beta-Binomial).
            # NULL counts → θ=1.0 (exactly the pre-feedback behavior); floored
            # at RELIABILITY_FLOOR so feedback discounts a rule, never erases
            # it. The critical fail-cap below is intentionally NOT θ-scaled.
            if rule is not None:
                points_deduction *= theta(
                    getattr(rule, "reliability_alpha", None),
                    getattr(rule, "reliability_beta", None),
                )

            # Scale by the model's confidence in the finding (audit H16).
            points_deduction *= ScoringService._confidence_weight(violation)
            violation["points_deduction"] = points_deduction
            enriched.append(violation)

        return enriched

    @staticmethod
    def _calculate_category_score(violations: List[Dict], category: str) -> float:
        base_score = 100.0
        category_violations = [
            v for v in violations
            if category == v.get("category", "") or category in v.get("category", "").split("|")
        ]

        if not category_violations:
            return 100.0

        total_deduction = sum(v.get("points_deduction", 0) for v in category_violations)

        if total_deduction > 100:
            scaled_deduction = 95 * (1 - (1 / (1 + total_deduction / 100)))
            base_score -= scaled_deduction
        else:
            base_score -= total_deduction

        return max(0.0, min(100.0, base_score))

    @staticmethod
    def _get_grade(score: float) -> str:
        if score >= 90: return "A"
        elif score >= 80: return "B"
        elif score >= 70: return "C"
        elif score >= 60: return "D"
        else: return "F"

    @staticmethod
    def _get_status(violations: List[Dict], overall_score: float) -> str:
        # Confidence-aware critical handling: a credible (high-confidence)
        # critical fails the verdict; a low-confidence critical is uncertain and
        # routes to "flagged" (human review) rather than auto-failing compliant
        # content on a shaky finding.
        criticals = [v for v in violations if v.get("severity") == "critical"]
        high_conf_critical = any(
            ScoringService._confidence_weight(v) >= ScoringService.CRITICAL_CONFIDENCE_THRESHOLD
            for v in criticals
        )
        if high_conf_critical or overall_score < 60:
            return "failed"
        if criticals or overall_score < 80:
            return "flagged"
        return "passed"


scoring_service = ScoringService()
