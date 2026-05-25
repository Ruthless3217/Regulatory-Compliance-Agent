"""
Dashboard & Analytics Routes

Summary views and quick stats for the compliance dashboard.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models.submission import Submission
from app.models.compliance_check import ComplianceCheck
from app.models.violation import Violation
from app.models.rule import Rule

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary")
async def get_dashboard_summary(db: Session = Depends(get_db)):
    """Overall compliance dashboard summary."""
    from datetime import datetime, timedelta, timezone

    total_submissions = db.query(Submission).count()
    total_checks = db.query(ComplianceCheck).count()
    total_violations = db.query(Violation).count()
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
    critical_count = db.query(Violation).filter(Violation.severity == "critical").count()

    # Auto-fix rate: share of violations the LLM marked auto_fixable.
    # Column is varchar — keep the IN list all-strings (mixing in a bool
    # raises `operator does not exist: character varying = boolean` on PG).
    if total_violations > 0:
        auto_fixable_count = db.query(Violation).filter(
            Violation.auto_fixable.in_(["true", "True", "TRUE", "1", "yes"])
        ).count()
        auto_fix_rate = round((auto_fixable_count / total_violations) * 100, 1)
    else:
        auto_fixable_count = 0
        auto_fix_rate = 0.0

    # Recent submissions
    recent = db.query(Submission).order_by(Submission.submitted_at.desc()).limit(5).all()

    return {
        "stats": {
            "total_submissions": total_submissions,
            "total_checks": total_checks,
            "total_violations": total_violations,
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


@router.get("/violations-by-category")
async def get_violations_by_category(db: Session = Depends(get_db)):
    """Get violation counts grouped by category."""
    result = db.query(
        Violation.category,
        func.count(Violation.id).label("count")
    ).group_by(Violation.category).all()

    return {
        "violations_by_category": [
            {"category": r.category, "count": r.count}
            for r in result
        ]
    }


@router.get("/violations-by-severity")
async def get_violations_by_severity(db: Session = Depends(get_db)):
    """Get violation counts grouped by severity."""
    result = db.query(
        Violation.severity,
        func.count(Violation.id).label("count")
    ).group_by(Violation.severity).all()

    return {
        "violations_by_severity": [
            {"severity": r.severity, "count": r.count}
            for r in result
        ]
    }
