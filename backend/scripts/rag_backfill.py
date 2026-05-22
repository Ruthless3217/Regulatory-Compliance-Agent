"""Idempotent RAG backfill.

Use cases:
  * First-time setup, after `alembic upgrade head` creates the rag_* tables.
  * After upgrading the embedding model.
  * After the eventual pgvector → Azure AI Search cutover: flip
    `RAG_VECTOR_BACKEND=azure_search`, then re-run this with `--all`.
  * Periodically, as a safety net for any indexer that failed silently.

Usage:
  python -m scripts.rag_backfill --rules
  python -m scripts.rag_backfill --chunks
  python -m scripts.rag_backfill --source-docs
  python -m scripts.rag_backfill --all

The script does NOT touch `rag_source_docs.derived_rule_ids`; that mapping
only exists for rules generated *after* RAG was installed. Historical
auto-generated rules without source-doc traces remain unlinked.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import List

from sqlalchemy.orm import Session

# Path bootstrap so the script runs both via `python -m scripts.rag_backfill`
# and as a bare file: `python scripts/rag_backfill.py`.
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal  # noqa: E402
from app.models.submission import Submission  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("rag_backfill")


async def backfill_rules() -> int:
    from app.services.rag.indexers.rules_indexer import upsert_all_active_rules
    db: Session = SessionLocal()
    try:
        n = await upsert_all_active_rules(db)
        logger.info(f"Backfilled {n} active rules into rag_rules")
        return n
    finally:
        db.close()


async def backfill_chunks() -> int:
    """Re-index every existing submission's chunks. Status mirrors the submission."""
    from app.services.rag.indexers.chunks_indexer import upsert_chunks_for_submission

    db: Session = SessionLocal()
    total_chunks = 0
    try:
        submissions: List[Submission] = db.query(Submission).all()
        for sub in submissions:
            status = "analyzed" if sub.status == "analyzed" else "analyzing"
            summary = f"{sub.title} (status={sub.status})"
            try:
                n = await upsert_chunks_for_submission(
                    submission_id=sub.id,
                    db=db,
                    submission_status=status,
                    submission_summary=summary,
                )
                total_chunks += n
                logger.info(f"  · {sub.title}: {n} chunks ({status})")
            except Exception as e:
                logger.error(f"  ✗ {sub.title}: {e}")
        logger.info(f"Backfilled {total_chunks} chunks across {len(submissions)} submissions")
        return total_chunks
    finally:
        db.close()


async def backfill_source_docs() -> int:
    """No-op for now: historical source documents were not retained as raw text
    in the rules table prior to this change. The next time a regulator PDF is
    ingested via `POST /rules/generate-from-document`, it will be indexed
    automatically.
    """
    logger.info(
        "Source-doc backfill skipped — historical regulator PDFs were not "
        "stored as raw text. Re-upload them via /rules/generate-from-document "
        "to populate rag_source_docs."
    )
    return 0


async def main() -> None:
    parser = argparse.ArgumentParser(description="RAG backfill utility")
    parser.add_argument("--rules", action="store_true", help="Re-embed all active rules")
    parser.add_argument("--chunks", action="store_true", help="Re-embed all submission chunks")
    parser.add_argument("--source-docs", action="store_true", help="Re-index source documents")
    parser.add_argument("--all", action="store_true", help="Run all backfills")
    args = parser.parse_args()

    if not any([args.rules, args.chunks, args.source_docs, args.all]):
        parser.print_help()
        sys.exit(0)

    if args.all or args.rules:
        await backfill_rules()
    if args.all or args.chunks:
        await backfill_chunks()
    if args.all or args.source_docs:
        await backfill_source_docs()


if __name__ == "__main__":
    asyncio.run(main())
