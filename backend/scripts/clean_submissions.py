"""One-time cleanup of the submissions table (and everything hanging off it).

Deletes submissions and lets the schema cascade:

  CASCADE   compliance_checks -> violations, analysis_runs, content_chunks,
            submission_revisions, document_comments
  SET NULL  llm_usage_events.submission_id, rule_feedback.submission_id

`rule_feedback` is deliberately NOT deleted. It is the reviewer-decision
history the precedent engine and rule reliability learn from; its rows survive
with a NULL submission_id, exactly as the schema intends. Deleting submissions
must not quietly delete the model's memory of what reviewers decided.

Uploaded files are removed too when --files is passed, because a submission row
is the only thing that references its upload — orphaned bytes in `upload_dir`
are otherwise unreachable and never cleaned up.

DRY RUN BY DEFAULT. Nothing is written without --apply.

    python -m scripts.clean_submissions                      # count what would go
    python -m scripts.clean_submissions --before 2026-01-01  # older than a date
    python -m scripts.clean_submissions --keep-approved      # spare signed-off work
    python -m scripts.clean_submissions --all --apply --files

Take a backup first — scripts/db_backup.sh — this has no undo.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import text

from app.database import SessionLocal
from app.models.submission import Submission

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("clean_submissions")


def _parse_date(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        raise SystemExit(f"--before expects an ISO date like 2026-01-01, got {value!r}")


def _select(db, before: Optional[datetime], keep_approved: bool, statuses: List[str]):
    q = db.query(Submission)
    if before is not None:
        q = q.filter(Submission.submitted_at < before)
    if keep_approved:
        q = q.filter(
            (Submission.approval_status.is_(None)) | (Submission.approval_status != "approved")
        )
    if statuses:
        q = q.filter(Submission.status.in_(statuses))
    return q.order_by(Submission.submitted_at.asc()).all()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="Every submission. Required if no other filter is given.")
    ap.add_argument("--before", metavar="ISO_DATE", help="Only submissions submitted before this date.")
    ap.add_argument("--status", action="append", default=[], metavar="STATUS",
                    help="Only these statuses (repeatable), e.g. --status failed --status uploaded.")
    ap.add_argument("--keep-approved", action="store_true", help="Never delete an approved submission.")
    ap.add_argument("--files", action="store_true", help="Also delete each submission's uploaded file.")
    ap.add_argument("--apply", action="store_true", help="Actually delete. Without this nothing is written.")
    args = ap.parse_args(argv)

    before = _parse_date(args.before) if args.before else None
    if not (args.all or before or args.status):
        ap.error("Refusing to run with no filter — pass --all if you really mean every submission.")

    db = SessionLocal()
    try:
        rows = _select(db, before, args.keep_approved, args.status)
        if not rows:
            logger.info("Nothing matches — no submissions deleted.")
            return 0

        # Counted before the delete, so the log states what was actually removed
        # rather than what the cascade was assumed to remove.
        ids = [str(r.id) for r in rows]
        counts = db.execute(
            text(
                """
                SELECT
                  (SELECT COUNT(*) FROM compliance_checks WHERE submission_id = ANY(CAST(:ids AS UUID[]))) AS checks,
                  (SELECT COUNT(*) FROM submission_revisions WHERE submission_id = ANY(CAST(:ids AS UUID[]))) AS revisions,
                  (SELECT COUNT(*) FROM analysis_runs WHERE submission_id = ANY(CAST(:ids AS UUID[]))) AS runs,
                  (SELECT COUNT(*) FROM rule_feedback WHERE submission_id = ANY(CAST(:ids AS UUID[]))) AS feedback
                """
            ),
            {"ids": ids},
        ).mappings().one()

        logger.info("%d submission(s) selected.", len(rows))
        logger.info("  cascade deletes: %d compliance check(s), %d revision(s), %d analysis run(s)",
                    counts["checks"], counts["revisions"], counts["runs"])
        logger.info("  kept, detached:  %d reviewer feedback row(s) (submission_id -> NULL)",
                    counts["feedback"])
        for r in rows[:20]:
            logger.info("  - %s  %s  %s", r.id, (r.status or "?").ljust(12), (r.title or "")[:60])
        if len(rows) > 20:
            logger.info("  … and %d more", len(rows) - 20)

        if not args.apply:
            logger.info("\nDRY RUN — nothing deleted. Re-run with --apply to commit.")
            return 0

        removed_files = 0
        if args.files:
            for r in rows:
                path = r.file_path
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                        removed_files += 1
                    except OSError as e:  # noqa: PERF203 — one bad file must not stop the run
                        logger.warning("  could not delete %s: %s", path, e)

        for r in rows:
            db.delete(r)
        db.commit()
        logger.info("Deleted %d submission(s); removed %d uploaded file(s).", len(rows), removed_files)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
