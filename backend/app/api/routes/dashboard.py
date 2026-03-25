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
    total_submissions = db.query(Submission).count()
    total_checks = db.query(ComplianceCheck).count()
    total_violations = db.query(Violation).count()
    total_rules = db.query(Rule).filter(Rule.is_active == True).count()

    # Score distribution
    checks = db.query(ComplianceCheck).all()
    if checks:
        avg_score = sum(c.overall_score for c in checks if c.overall_score) / len(checks)
        grade_dist = {}
        for c in checks:
            if c.grade:
                grade_dist[c.grade] = grade_dist.get(c.grade, 0) + 1
    else:
        avg_score = 0
        grade_dist = {}

    # Recent submissions
    recent = db.query(Submission).order_by(Submission.submitted_at.desc()).limit(5).all()

    return {
        "stats": {
            "total_submissions": total_submissions,
            "total_checks": total_checks,
            "total_violations": total_violations,
            "active_rules": total_rules,
            "average_score": round(avg_score, 2)
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
