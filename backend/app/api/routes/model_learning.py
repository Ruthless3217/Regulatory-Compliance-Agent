"""
Model Learning Routes

Read-only diagnostics for the "adaptive rule weights" pipeline: how many
findings get reviewed, how precise each rule/category/severity is against
reviewer verdicts, whether the system's own score tracks the reviewer's
held-out score, per-rule reliability history, analysis latency/cost, and
which rules keep recurring across many submissions.

Deliberately NOT "governed continuous learning" dashboards: there is no
approval gate, no training pipeline, no model retraining here — just the
existing Beta-Binomial rule-weight update (rule_feedback_service.py) made
visible. Every endpoint says so, and returns a null/'insufficient_data'
shape rather than a fabricated number when the underlying data doesn't
exist yet (e.g. no reviewer-scored checks, no reliability-event rows).

Endpoints:
- GET /model-learning/funnel                    - flagged -> reviewed -> applied-to-scoring counts
- GET /model-learning/precision?by=             - reviewer-verdict precision by rule|category|severity
- GET /model-learning/calibration               - |system score - reviewer score| convergence data
- GET /model-learning/rule-reliability-history  - one rule's alpha/beta/theta history (rule_reliability_events)
- GET /model-learning/latency                   - analysis_runs duration/token/cost stats
- GET /model-learning/repeated-patterns         - rules firing across many distinct submissions
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models.violation import Violation
from app.models.compliance_check import ComplianceCheck
from app.models.rule import Rule
from app.models.rule_feedback import RuleFeedback
from app.models.analysis_run import AnalysisRun
from app.models.rule_reliability_event import RuleReliabilityEvent
from app.services.agents.compliance.reliability import theta
from app.auth.dependencies import require

router = APIRouter(prefix="/model-learning", tags=["Model Learning"])

# RuleFeedback.verdict carries two vocabularies: the legacy accept/reject
# shim (POST .../feedback) and the real correct/not_violation/dismiss
# taxonomy (POST .../actions). 'dismiss' carries no correctness signal and
# is excluded from precision math on purpose.
_POSITIVE_VERDICTS = {"accept", "correct"}
_NEGATIVE_VERDICTS = {"reject", "not_violation"}
_SCORING_VERDICTS = _POSITIVE_VERDICTS | _NEGATIVE_VERDICTS


def _truncate(text: Optional[str], n: int = 140) -> Optional[str]:
    if text is None:
        return None
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _percentile(sorted_vals: List[float], pct: float) -> Optional[float]:
    """Linear-interpolation percentile over an already-sorted list."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return round(float(d0 + d1), 1)


def _verdict_counts_by_key(db: Session, by: str) -> Dict[str, Dict[str, int]]:
    """RuleFeedback verdict counts grouped by rule_id, Violation.category, or
    Violation.severity — one query, no N+1 per group."""
    if by == "rule":
        rows = (
            db.query(RuleFeedback.rule_id.label("key"), RuleFeedback.verdict, func.count(RuleFeedback.id))
            .filter(RuleFeedback.rule_id.isnot(None))
            .group_by(RuleFeedback.rule_id, RuleFeedback.verdict)
            .all()
        )
    else:
        col = Violation.category if by == "category" else Violation.severity
        rows = (
            db.query(col.label("key"), RuleFeedback.verdict, func.count(RuleFeedback.id))
            .join(Violation, Violation.id == RuleFeedback.violation_id)
            .group_by(col, RuleFeedback.verdict)
            .all()
        )

    grouped: Dict[str, Dict[str, int]] = {}
    for key, verdict, count in rows:
        k = str(key) if key is not None else "unknown"
        grouped.setdefault(k, {})[verdict] = int(count)
    return grouped


def _precision_from_counts(counts: Dict[str, int]) -> Dict:
    positive = sum(c for v, c in counts.items() if v in _POSITIVE_VERDICTS)
    negative = sum(c for v, c in counts.items() if v in _NEGATIVE_VERDICTS)
    total = positive + negative
    return {
        "positive": positive,
        "negative": negative,
        "total": total,
        "precision": round(positive / total, 4) if total else None,
    }


@router.get("/funnel")
async def get_learning_funnel(
    user: dict = Depends(require("dashboard:view")),
    db: Session = Depends(get_db),
):
    """Flags -> Awaiting review -> Feedback collected -> Applied to scoring.

    Counts only non-suppressed violations (suppressed findings never reach a
    reviewer). 'Applied to scoring' is the subset of reviewed findings whose
    verdict actually moved a rule's Beta-Binomial weight (correct/not_violation
    with a linked rule; 'dismiss' never does).
    """
    flagged = (
        db.query(func.count(Violation.id))
        .filter(Violation.suppressed == False)  # noqa: E712 - matches existing convention
        .scalar()
        or 0
    )
    feedback_collected = (
        db.query(func.count(func.distinct(RuleFeedback.violation_id)))
        .join(Violation, Violation.id == RuleFeedback.violation_id)
        .filter(Violation.suppressed == False)  # noqa: E712
        .scalar()
        or 0
    )
    applied_to_scoring = (
        db.query(func.count(func.distinct(RuleFeedback.violation_id)))
        .join(Violation, Violation.id == RuleFeedback.violation_id)
        .filter(
            Violation.suppressed == False,  # noqa: E712
            RuleFeedback.rule_id.isnot(None),
            RuleFeedback.verdict.in_(_SCORING_VERDICTS),
        )
        .scalar()
        or 0
    )
    awaiting_review = max(flagged - feedback_collected, 0)

    return {
        "flagged": flagged,
        "awaiting_review": awaiting_review,
        "feedback_collected": feedback_collected,
        "applied_to_scoring": applied_to_scoring,
        "no_gate_warning": (
            "Correct / Not-a-violation verdicts update the rule's reliability "
            "weight immediately on submission — there is no approval gate "
            "between 'feedback collected' and 'applied to scoring'."
        ),
        "status": "computed",
    }


@router.get("/precision")
async def get_precision(
    by: str = Query("rule", pattern="^(rule|category|severity)$"),
    user: dict = Depends(require("dashboard:view")),
    db: Session = Depends(get_db),
):
    """Reviewer-verdict precision (correct / (correct + not_violation)),
    grouped by rule, violation category, or violation severity."""
    grouped = _verdict_counts_by_key(db, by)

    rule_meta: Dict[str, Rule] = {}
    if by == "rule" and grouped:
        rules = db.query(Rule).filter(Rule.id.in_(list(grouped.keys()))).all()
        rule_meta = {str(r.id): r for r in rules}

    groups = []
    for key, counts in grouped.items():
        stats = _precision_from_counts(counts)
        entry = {
            "key": key,
            "correct": stats["positive"],
            "not_violation": stats["negative"],
            "reviewed_total": stats["total"],
            "precision": stats["precision"],
            "status": "computed" if stats["total"] else "insufficient_data",
        }
        if by == "rule":
            rule = rule_meta.get(key)
            entry["rule_text"] = _truncate(rule.rule_text) if rule else None
            entry["category"] = rule.category if rule else None
            entry["severity"] = rule.severity if rule else None
        groups.append(entry)

    groups.sort(key=lambda g: g["reviewed_total"], reverse=True)

    return {
        "by": by,
        "groups": groups,
        "status": "computed" if groups else "insufficient_data",
        "note": (
            "Precision = correct verdicts / (correct + not_violation verdicts) "
            "reviewed so far; 'dismiss' actions carry no correctness signal "
            "and are excluded from the denominator."
        ),
    }


@router.get("/calibration")
async def get_calibration(
    user: dict = Depends(require("dashboard:view")),
    db: Session = Depends(get_db),
):
    """|system overall_score - reviewer_score| convergence data — the ONLY
    evidence of whether adaptive rule weights actually improve the system,
    since reviewer_score is deliberately held out of training. Empty until a
    reviewer has scored at least one check via
    POST /compliance/check/{id}/reviewer-score.
    """
    checks = (
        db.query(ComplianceCheck)
        .filter(ComplianceCheck.reviewer_score.isnot(None), ComplianceCheck.overall_score.isnot(None))
        .order_by(ComplianceCheck.checked_at.asc())
        .all()
    )
    n = len(checks)
    if n == 0:
        return {
            "sample_size": 0,
            "status": "insufficient_data",
            "mean_absolute_gap": None,
            "mean_system_score": None,
            "mean_reviewer_score": None,
            "points": [],
            "note": (
                "No reviewer-scored checks yet — nobody has called "
                "POST /compliance/check/{id}/reviewer-score. Calibration "
                "cannot be shown until at least one document is scored."
            ),
        }

    gaps = [abs(c.overall_score - c.reviewer_score) for c in checks]
    points = [
        {
            "check_id": str(c.id),
            "checked_at": c.checked_at.isoformat() if c.checked_at else None,
            "system_score": c.overall_score,
            "reviewer_score": c.reviewer_score,
            "gap": abs(c.overall_score - c.reviewer_score),
        }
        for c in checks[-200:]
    ]
    return {
        "sample_size": n,
        "status": "computed",
        "mean_absolute_gap": round(sum(gaps) / n, 3),
        "mean_system_score": round(sum(c.overall_score for c in checks) / n, 3),
        "mean_reviewer_score": round(sum(c.reviewer_score for c in checks) / n, 3),
        "points": points,
        "note": "reviewer_score is held-out evaluation data — never an input to scoring or rule weights.",
    }


@router.get("/rule-reliability-history")
async def get_rule_reliability_history(
    rule_id: str = Query(...),
    user: dict = Depends(require("dashboard:view")),
    db: Session = Depends(get_db),
):
    """One rule's append-only Beta-Binomial before/after history
    (rule_reliability_events, migration 0029)."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    events = (
        db.query(RuleReliabilityEvent)
        .filter(RuleReliabilityEvent.rule_id == rule_id)
        .order_by(RuleReliabilityEvent.created_at.asc())
        .all()
    )

    return {
        "rule_id": str(rule.id),
        "rule_text": _truncate(rule.rule_text),
        "current_alpha": float(rule.reliability_alpha) if rule.reliability_alpha is not None else None,
        "current_beta": float(rule.reliability_beta) if rule.reliability_beta is not None else None,
        "current_theta": theta(rule.reliability_alpha, rule.reliability_beta),
        "events": [
            {
                "id": str(e.id),
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "rule_feedback_id": str(e.rule_feedback_id) if e.rule_feedback_id else None,
                "alpha_before": e.alpha_before,
                "beta_before": e.beta_before,
                "theta_before": e.theta_before,
                "alpha_after": e.alpha_after,
                "beta_after": e.beta_after,
                "theta_after": e.theta_after,
            }
            for e in events
        ],
        "status": "computed" if events else "insufficient_data",
        "note": (
            "Chronological before/after reliability snapshots for this rule."
            if events
            else "No reliability-event rows exist for this rule yet."
        ),
    }


@router.get("/latency")
async def get_latency(
    user: dict = Depends(require("dashboard:view")),
    db: Session = Depends(get_db),
):
    """Analysis duration/token/cost stats over completed analysis_runs."""
    completed = (
        db.query(
            AnalysisRun.duration_ms,
            AnalysisRun.prompt_tokens,
            AnalysisRun.completion_tokens,
            AnalysisRun.total_cost_usd,
            AnalysisRun.trigger_source,
        )
        .filter(AnalysisRun.status == "completed", AnalysisRun.duration_ms.isnot(None))
        .all()
    )
    n = len(completed)
    if n == 0:
        return {
            "sample_size": 0,
            "status": "insufficient_data",
            "avg_duration_ms": None,
            "p50_duration_ms": None,
            "p95_duration_ms": None,
            "avg_prompt_tokens": None,
            "avg_completion_tokens": None,
            "avg_cost_usd": None,
            "by_trigger_source": [],
            "note": "No completed analysis runs recorded yet.",
        }

    durations = sorted(r.duration_ms for r in completed)
    by_source: Dict[str, List[int]] = {}
    for r in completed:
        by_source.setdefault(r.trigger_source or "unknown", []).append(r.duration_ms)

    return {
        "sample_size": n,
        "status": "computed",
        "avg_duration_ms": round(sum(durations) / n, 1),
        "p50_duration_ms": _percentile(durations, 0.5),
        "p95_duration_ms": _percentile(durations, 0.95),
        "avg_prompt_tokens": round(sum(r.prompt_tokens or 0 for r in completed) / n, 1),
        "avg_completion_tokens": round(sum(r.completion_tokens or 0 for r in completed) / n, 1),
        "avg_cost_usd": round(float(sum(r.total_cost_usd or 0 for r in completed)) / n, 4),
        "by_trigger_source": [
            {"trigger_source": src, "count": len(vals), "avg_duration_ms": round(sum(vals) / len(vals), 1)}
            for src, vals in sorted(by_source.items())
        ],
        "note": "Computed over completed analysis runs only; failed/needs_review runs are excluded.",
    }


@router.get("/repeated-patterns")
async def get_repeated_patterns(
    min_submissions: int = Query(2, ge=1, le=100),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(require("dashboard:view")),
    db: Session = Depends(get_db),
):
    """Rules that fired on many DISTINCT submissions (a recurring pattern),
    not just a high raw violation count in one document — distinct from
    dashboard.py's /top-rules, which ranks by raw violation count alone."""
    rows = (
        db.query(
            Violation.rule_id.label("rule_id"),
            func.count(func.distinct(ComplianceCheck.submission_id)).label("submission_count"),
            func.count(Violation.id).label("violation_count"),
        )
        .join(ComplianceCheck, ComplianceCheck.id == Violation.compliance_check_id)
        .filter(Violation.rule_id.isnot(None), Violation.suppressed == False)  # noqa: E712
        .group_by(Violation.rule_id)
        .having(func.count(func.distinct(ComplianceCheck.submission_id)) >= min_submissions)
        .order_by(func.count(func.distinct(ComplianceCheck.submission_id)).desc())
        .limit(limit)
        .all()
    )

    if not rows:
        return {
            "min_submissions": min_submissions,
            "patterns": [],
            "status": "insufficient_data",
            "note": f"No rule has fired on {min_submissions}+ distinct submissions yet.",
        }

    rule_ids = [r.rule_id for r in rows]
    rules = {r.id: r for r in db.query(Rule).filter(Rule.id.in_(rule_ids)).all()}
    verdict_counts = _verdict_counts_by_key(db, "rule")

    patterns = []
    for r in rows:
        rule = rules.get(r.rule_id)
        stats = _precision_from_counts(verdict_counts.get(str(r.rule_id), {}))
        patterns.append(
            {
                "rule_id": str(r.rule_id),
                "rule_text": _truncate(rule.rule_text) if rule else None,
                "category": rule.category if rule else None,
                "severity": rule.severity if rule else None,
                "submission_count": int(r.submission_count),
                "violation_count": int(r.violation_count),
                "reliability_theta": theta(rule.reliability_alpha, rule.reliability_beta) if rule else None,
                "reviewer_precision": stats["precision"],
                "reviewed_count": stats["total"],
            }
        )

    return {
        "min_submissions": min_submissions,
        "patterns": patterns,
        "status": "computed",
        "note": "Rules firing across multiple distinct submissions — a recurring pattern, not necessarily a false positive.",
    }
