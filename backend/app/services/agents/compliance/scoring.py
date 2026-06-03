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

    @staticmethod
    @traceable(run_type="tool", name="Scoring.calculate_scores")
    def calculate_scores(
        violations: List[Dict],
        db: Optional[Session] = None,
        project_config: Optional[Dict] = None,
        categories: Optional[List[str]] = None
    ) -> Dict[str, float]:
        """
        Calculate compliance scores based on violations.
        Supports dynamic weights from project config or auto-discovers categories.
        """
        enriched_violations = ScoringService._enrich_violations_with_points(violations, db)
        weights = ScoringService._resolve_weights(enriched_violations, project_config, categories)

        category_scores = {}
        for category in weights.keys():
            category_scores[category] = ScoringService._calculate_category_score(
                enriched_violations, category
            )

        if not weights:
            overall_score = 100.0
        else:
            overall_score = sum(
                category_scores.get(cat, 100.0) * weight
                for cat, weight in weights.items()
            )

        grade = ScoringService._get_grade(overall_score)
        status = ScoringService._get_status(enriched_violations, overall_score)

        result = {
            "overall": max(0.0, min(100.0, round(overall_score, 2))),
            "grade": grade,
            "status": status
        }

        for cat, score in category_scores.items():
            result[cat] = round(score, 2)

        return result

    @staticmethod
    def _resolve_weights(
        violations: List[Dict],
        project_config: Optional[Dict],
        categories: Optional[List[str]] = None
    ) -> Dict[str, float]:
        if project_config and project_config.get("weights"):
            return ScoringService._normalize_weights(project_config["weights"])

        discovered_categories = set()
        for v in violations:
            cat = v.get("category", "general")
            for c in cat.split("|"):
                discovered_categories.add(c.strip())

        if discovered_categories:
            equal_weight = 1.0 / len(discovered_categories)
            return {cat: equal_weight for cat in discovered_categories}

        if categories:
            equal_weight = 1.0 / len(categories)
            return {cat: equal_weight for cat in categories}

        return {}

    @staticmethod
    def _normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
        if not weights:
            return {}
        total = sum(weights.values())
        if total == 0:
            return {k: 1.0 / len(weights) for k in weights}
        return {k: v / total for k, v in weights.items()}

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

        enriched = []
        for violation in violations:
            rule_id = violation.get("rule_id")
            points_deduction = None

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
        has_critical = any(v.get("severity") == "critical" for v in violations)
        if has_critical or overall_score < 60:
            return "failed"
        elif overall_score < 80:
            return "flagged"
        else:
            return "passed"


scoring_service = ScoringService()
