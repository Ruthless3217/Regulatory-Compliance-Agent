"""
Dashboard & Analytics Routes

Summary views and quick stats for the compliance dashboard.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models.submission import Submission
from app.models.compliance_check import ComplianceCheck
from app.models.violation import Violation
from app.models.rule import Rule
from app.auth.dependencies import require

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary")
async def get_dashboard_summary(user: dict = Depends(require("dashboard:view")), db: Session = Depends(get_db)):
    """Overall compliance dashboard summary."""
    from datetime import datetime, timedelta, timezone

    total_submissions = db.query(Submission).count()
    total_checks = db.query(ComplianceCheck).count()
    total_findings = db.query(Violation).count()
    scored_violations = db.query(Violation).filter(
        Violation.suppressed == False  # noqa: E712 - SQLAlchemy predicate
    ).count()
    suppressed_findings = db.query(Violation).filter(
        Violation.suppressed == True  # noqa: E712 - SQLAlchemy predicate
    ).count()
    total_rules = db.query(Rule).filter(Rule.is_active == True).count()

    # Score distribution
    checks = db.query(ComplianceCheck).all()
    if checks:
        scored = [c.overall_score for c in checks if c.overall_score is not None]
        avg_score = sum(scored) / len(scored) if scored else 0.0
        grade_dist = {}
        for c in checks:
            if c.grade:
                grade_dist[c.grade] = grade_dist.get(c.grade, 0) + 1
    else:
        avg_score = 0.0
        grade_dist = {}

    # Submissions in the last 7 days
    one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    submissions_this_week = db.query(Submission).filter(
        Submission.submitted_at >= one_week_ago
    ).count()

    # Critical violations (open)
    critical_count = db.query(Violation).filter(
        Violation.severity == "critical",
        Violation.suppressed == False,  # noqa: E712
    ).count()

    # Auto-fix rate: share of violations the LLM marked auto_fixable.
    # Column is varchar — keep the IN list all-strings (mixing in a bool
    # raises `operator does not exist: character varying = boolean` on PG).
    if scored_violations > 0:
        auto_fixable_count = db.query(Violation).filter(
            Violation.auto_fixable.in_(["true", "True", "TRUE", "1", "yes"]),
            Violation.suppressed == False,  # noqa: E712
        ).count()
        auto_fix_rate = round((auto_fixable_count / scored_violations) * 100, 1)
    else:
        auto_fixable_count = 0
        auto_fix_rate = 0.0

    # Recent submissions
    recent = db.query(Submission).order_by(Submission.submitted_at.desc()).limit(5).all()

    return {
        "stats": {
            "total_submissions": total_submissions,
            "total_checks": total_checks,
            # total_violations remains for API compatibility, with the
            # explicit scored-only meaning used by charts and the report.
            "total_violations": scored_violations,
            "scored_violations": scored_violations,
            "suppressed_findings": suppressed_findings,
            "total_findings": total_findings,
            "active_rules": total_rules,
            "average_score": round(avg_score, 2),
            "submissions_this_week": submissions_this_week,
            "critical_count": critical_count,
            "auto_fix_rate": auto_fix_rate,
            "auto_fixable_count": auto_fixable_count,
        },
        "grade_distribution": grade_dist,
        "recent_submissions": [
            {
                "id": str(s.id),
                "title": s.title,
                "status": s.status,
                "submitted_at": s.submitted_at.isoformat()
            }
            for s in recent
        ]
    }


@router.get("/timeseries")
async def get_dashboard_timeseries(
    bucket: str = Query("day", pattern="^(day|week)$"),
    user: dict = Depends(require("dashboard:view")),
    db: Session = Depends(get_db),
):
    """Time-series of submissions, average score, and violations bucketed by
    day or week. Aggregated on the fly via Postgres date_trunc; no new tables.
    Returns up to the most recent 90 periods, sorted ascending by period."""
    # Submissions per period.
    sub_period = func.date_trunc(bucket, Submission.submitted_at).label("period")
    sub_rows = (
        db.query(sub_period, func.count(Submission.id).label("submission_count"))
        .group_by(sub_period)
        .all()
    )

    # Checks per period → avg score + count.
    chk_period = func.date_trunc(bucket, ComplianceCheck.checked_at).label("period")
    chk_rows = (
        db.query(
            chk_period,
            func.avg(ComplianceCheck.overall_score).label("avg_score"),
            func.count(ComplianceCheck.id).label("check_count"),
        )
        .group_by(chk_period)
        .all()
    )

    # Scored findings and the suppressed human-review lane per period.
    vio_period = func.date_trunc(bucket, Violation.created_at).label("period")
    vio_rows = (
        db.query(vio_period, func.count(Violation.id).label("violation_count"))
        .filter(Violation.suppressed == False)  # noqa: E712
        .group_by(vio_period)
        .all()
    )
    suppressed_rows = (
        db.query(vio_period, func.count(Violation.id).label("suppressed_count"))
        .filter(Violation.suppressed == True)  # noqa: E712
        .group_by(vio_period)
        .all()
    )

    # Merge the three aggregations keyed by the truncated period.
    points: dict = {}

    def _key(period):
        return period.isoformat() if period is not None else None

    for r in sub_rows:
        k = _key(r.period)
        if k is None:
            continue
        points.setdefault(
            k, {"period": k, "submission_count": 0, "avg_score": None,
                "violation_count": 0, "suppressed_count": 0, "finding_count": 0}
        )
        points[k]["submission_count"] = int(r.submission_count or 0)

    for r in chk_rows:
        k = _key(r.period)
        if k is None:
            continue
        points.setdefault(
            k, {"period": k, "submission_count": 0, "avg_score": None,
                "violation_count": 0, "suppressed_count": 0, "finding_count": 0}
        )
        points[k]["avg_score"] = (
            round(float(r.avg_score), 2) if r.avg_score is not None else None
        )

    for r in vio_rows:
        k = _key(r.period)
        if k is None:
            continue
        points.setdefault(
            k, {"period": k, "submission_count": 0, "avg_score": None,
                "violation_count": 0, "suppressed_count": 0, "finding_count": 0}
        )
        points[k]["violation_count"] = int(r.violation_count or 0)
        points[k]["finding_count"] += int(r.violation_count or 0)

    for r in suppressed_rows:
        k = _key(r.period)
        if k is None:
            continue
        points.setdefault(
            k, {"period": k, "submission_count": 0, "avg_score": None,
                "violation_count": 0, "suppressed_count": 0, "finding_count": 0}
        )
        points[k]["suppressed_count"] = int(r.suppressed_count or 0)
        points[k]["finding_count"] += int(r.suppressed_count or 0)

    ordered = sorted(points.values(), key=lambda p: p["period"])
    # Keep the most recent 90 periods.
    ordered = ordered[-90:]

    return {"bucket": bucket, "points": ordered}


@router.get("/top-rules")
async def get_top_rules(
    limit: int = Query(10, ge=1, le=50),
    user: dict = Depends(require("dashboard:view")),
    db: Session = Depends(get_db),
):
    """Most-frequently-violated rules, joined to Rule metadata."""
    rows = (
        db.query(
            Violation.rule_id.label("rule_id"),
            func.count(Violation.id).label("count"),
            Rule.category.label("category"),
            Rule.severity.label("severity"),
            Rule.rule_text.label("rule_text"),
        )
        .join(Rule, Rule.id == Violation.rule_id)
        .filter(
            Violation.rule_id.isnot(None),
            Violation.suppressed == False,  # noqa: E712
        )
        .group_by(Violation.rule_id, Rule.category, Rule.severity, Rule.rule_text)
        .order_by(func.count(Violation.id).desc())
        .limit(limit)
        .all()
    )

    def _truncate(text, n=140):
        if text is None:
            return None
        return text if len(text) <= n else text[: n - 1].rstrip() + "…"

    return {
        "top_rules": [
            {
                "rule_id": str(r.rule_id),
                "category": r.category,
                "severity": r.severity,
                "rule_text": _truncate(r.rule_text),
                "count": int(r.count),
            }
            for r in rows
        ]
    }


@router.get("/violations-by-category")
async def get_violations_by_category(user: dict = Depends(require("dashboard:view")), db: Session = Depends(get_db)):
    """Get violation counts grouped by category."""
    result = db.query(
        Violation.category,
        func.count(Violation.id).label("count")
    ).filter(
        Violation.suppressed == False  # noqa: E712
    ).group_by(Violation.category).all()

    return {
        "violations_by_category": [
            {"category": r.category, "count": r.count}
            for r in result
        ]
    }


@router.get("/violations-by-severity")
async def get_violations_by_severity(user: dict = Depends(require("dashboard:view")), db: Session = Depends(get_db)):
    """Get violation counts grouped by severity."""
    result = db.query(
        Violation.severity,
        func.count(Violation.id).label("count")
    ).filter(
        Violation.suppressed == False  # noqa: E712
    ).group_by(Violation.severity).all()

    return {
        "violations_by_severity": [
            {"severity": r.severity, "count": r.count}
            for r in result
        ]
    }
