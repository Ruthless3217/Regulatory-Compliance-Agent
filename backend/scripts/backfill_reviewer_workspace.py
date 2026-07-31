"""Idempotent backfill for the interactive-review-workspace migrations (0023-0029).

Run ONCE, after `alembic upgrade head` and after taking a DB backup
(`scripts/db_backup.sh`). Safe to re-run — every UPDATE is guarded by
`WHERE <column> IS NULL`, so already-backfilled or freshly-created rows are
left untouched.

What this DOES backfill (mechanically derivable from data that already
exists, nothing invented):
  * violations.analysis_run_id  <- analysis_runs.compliance_check_id join.
  * violations.review_status/resolved_at <- the violation's own latest
    rule_feedback row, if one exists (pre-dates the reviewer-action taxonomy,
    so it's stamped "actioned" rather than a specific correct/not_violation/
    dismiss value, which was never recorded for old rows).
  * submissions.current_content <- original_content, so "the live document"
    resolves correctly for submissions created before this feature existed.

What this explicitly DOES NOT backfill, and why (mechanical honesty over a
fabricated-looking green field):
  * violations.anchor_page/anchor_bbox/section_title -- no page-tracking ever
    existed in the chunking pipeline for historical submissions; there is no
    source data to derive this from without re-processing the original file.
  * rule_feedback's snapshot columns (model_version, kb_version,
    confidence_snapshot, retrieval_snapshot) for pre-existing feedback rows --
    these describe state AT THE TIME the feedback was given, which was never
    captured. Backfilling with today's values would misrepresent history.
  * rule_reliability_events -- an append-only log of each feedback event's
    alpha/beta delta. No historical deltas were ever recorded, so there is
    nothing truthful to backfill; synthesizing one row from today's cumulative
    Rule.reliability_alpha/beta would fabricate a history that didn't happen.
  * Disclaimer/product-context re-verdicts for already-analyzed submissions --
    the derive_product_context bug fix changes what a NEW analysis run
    computes. Applying it retroactively means re-running the LLM pipeline
    against old documents and overwriting a historical compliance verdict --
    a business/compliance decision, not a mechanical backfill. Not done here.
  * precedent_cases.product_category re-tagging with the improved Par/Non-Par
    heuristic -- deliberately left out of this script; it touches the RAG
    precedent store (adjacent to, though not itself, the embedding column) and
    deserves its own reviewed pass rather than a guess bundled in here.

Usage:
  python -m scripts.backfill_reviewer_workspace --dry-run   # report counts only
  python -m scripts.backfill_reviewer_workspace --all
  python -m scripts.backfill_reviewer_workspace --violation-run-ids --submission-current-content
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("backfill_reviewer_workspace")


def backfill_violation_run_ids(db: Session, dry_run: bool) -> int:
    """violations.analysis_run_id <- the AnalysisRun whose compliance_check_id
    matches this violation's compliance_check_id (run_tracker.close_run has
    always set that link for every successfully-persisted run)."""
    count_sql = text(
        """
        SELECT count(*) FROM violations v
        JOIN analysis_runs ar ON ar.compliance_check_id = v.compliance_check_id
        WHERE v.analysis_run_id IS NULL
        """
    )
    n = db.execute(count_sql).scalar_one()
    logger.info(f"violations.analysis_run_id: {n} rows derivable from analysis_runs")
    if n and not dry_run:
        db.execute(
            text(
                """
                UPDATE violations v
                SET analysis_run_id = ar.id
                FROM analysis_runs ar
                WHERE ar.compliance_check_id = v.compliance_check_id
                  AND v.analysis_run_id IS NULL
                """
            )
        )
        db.commit()
        logger.info(f"violations.analysis_run_id: backfilled {n} rows")
    return n


def backfill_violation_review_status(db: Session, dry_run: bool) -> int:
    """violations.review_status/resolved_at <- the violation's own latest
    rule_feedback row (by updated_at), if any. Pre-0023 feedback only ever
    recorded accept/reject, never the current taxonomy, so this is stamped
    generically as 'actioned' rather than correct/not_violation/dismiss --
    do not invent which one it was."""
    count_sql = text(
        """
        SELECT count(*) FROM violations v
        WHERE v.review_status IS NULL
          AND EXISTS (SELECT 1 FROM rule_feedback rf WHERE rf.violation_id = v.id)
        """
    )
    n = db.execute(count_sql).scalar_one()
    logger.info(f"violations.review_status: {n} rows have prior feedback but no review_status")
    if n and not dry_run:
        db.execute(
            text(
                """
                UPDATE violations v
                SET review_status = 'actioned',
                    resolved_at = latest.updated_at
                FROM (
                    SELECT DISTINCT ON (violation_id) violation_id, updated_at
                    FROM rule_feedback
                    ORDER BY violation_id, updated_at DESC
                ) latest
                WHERE latest.violation_id = v.id
                  AND v.review_status IS NULL
                """
            )
        )
        db.commit()
        logger.info(f"violations.review_status: backfilled {n} rows")
    return n


def backfill_submission_current_content(db: Session, dry_run: bool) -> int:
    """submissions.current_content <- original_content, so the app's
    `current_content ?? original_content` convention resolves to the right
    text for submissions created before this column existed."""
    count_sql = text("SELECT count(*) FROM submissions WHERE current_content IS NULL")
    n = db.execute(count_sql).scalar_one()
    logger.info(f"submissions.current_content: {n} rows to seed from original_content")
    if n and not dry_run:
        db.execute(
            text(
                "UPDATE submissions SET current_content = original_content WHERE current_content IS NULL"
            )
        )
        db.commit()
        logger.info(f"submissions.current_content: backfilled {n} rows")
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--violation-run-ids", action="store_true")
    parser.add_argument("--violation-review-status", action="store_true")
    parser.add_argument("--submission-current-content", action="store_true")
    parser.add_argument("--all", action="store_true", help="run every backfill above")
    parser.add_argument("--dry-run", action="store_true", help="report counts only, write nothing")
    args = parser.parse_args()

    if not (args.all or args.violation_run_ids or args.violation_review_status or args.submission_current_content):
        parser.error("pick at least one target, or --all")

    db = SessionLocal()
    try:
        if args.dry_run:
            logger.info("DRY RUN — no writes will be made")
        if args.all or args.violation_run_ids:
            backfill_violation_run_ids(db, args.dry_run)
        if args.all or args.violation_review_status:
            backfill_violation_review_status(db, args.dry_run)
        if args.all or args.submission_current_content:
            backfill_submission_current_content(db, args.dry_run)
    finally:
        db.close()


if __name__ == "__main__":
    main()
