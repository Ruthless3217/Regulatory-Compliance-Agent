"""Ingest compliance guideline markdown files from docs/guidelines-docs/.

For each .md file, the script:
  1. Reads the content
  2. Calls rule_generator_service.generate_rules_from_text() which:
     - extracts compliance rules with the LLM
     - persists them to the rules table
     - indexes the source passages into rag_source_docs
     - indexes each generated rule into rag_rules

Idempotency: rules are re-extracted on every run. To avoid duplicates,
delete previously-ingested rules first (filter by metadata.source) or pass
--skip-existing which skips a file if any rule already exists with the
same source filename in its metadata.

Filename → regulator mapping (used only for source-doc tagging):
  retirement_and_pension_*  → irdai
  term_insurance_*          → irdai
  ulip_*                    → sebi
  tax_and_gst_*             → regulatory
  (anything else)           → regulatory

Usage:
  docker exec compliance-backend python -m scripts.ingest_guidelines
  docker exec compliance-backend python -m scripts.ingest_guidelines --skip-existing
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

# Path bootstrap so this runs via `python -m scripts.ingest_guidelines`.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal  # noqa: E402
from app.models.rule import Rule  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ingest_guidelines")

# Container path. Mounted from host's D:/Regulatory-Compliance-Agent/docs/guidelines-docs/
# via the docker-compose volume; falls back to a host-relative path for non-Docker runs.
DEFAULT_GUIDELINES_DIRS = [
    "/app/docs/guidelines-docs",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "guidelines-docs")),
]


def _regulator_for(filename: str) -> str:
    name = filename.lower()
    if name.startswith("retirement_and_pension") or name.startswith("term_insurance"):
        return "irdai"
    if name.startswith("ulip"):
        return "sebi"
    return "regulatory"


def _find_guidelines_dir() -> Path:
    for candidate in DEFAULT_GUIDELINES_DIRS:
        p = Path(candidate)
        if p.exists() and p.is_dir():
            return p
    raise FileNotFoundError(
        "Could not find docs/guidelines-docs/ at any of: "
        + ", ".join(DEFAULT_GUIDELINES_DIRS)
    )


def _already_ingested(db, source_name: str) -> bool:
    """Check whether any rule already cites this source filename."""
    # rule_metadata is JSONB; checking via SQL contains is cleaner but Python-side
    # iteration is fine for our small corpus.
    rules = db.query(Rule).filter(Rule.is_auto_generated == True).all()  # noqa: E712
    for r in rules:
        meta = r.rule_metadata or {}
        if isinstance(meta, dict) and meta.get("source") == source_name:
            return True
    return False


async def ingest_file(path: Path, skip_existing: bool) -> dict:
    from app.services.rule_generator_service import rule_generator_service

    title = path.stem.replace("_", " ").title()
    source_name = path.name
    regulator = _regulator_for(path.name)

    db = SessionLocal()
    try:
        if skip_existing and _already_ingested(db, source_name):
            logger.info(f"  ⤵ skip (already ingested): {source_name}")
            return {"file": source_name, "skipped": True, "rules_created": 0}

        content = path.read_text(encoding="utf-8", errors="replace")
        logger.info(f"  → {source_name} ({len(content)} chars, regulator={regulator})")

        result = await rule_generator_service.generate_rules_from_text(
            document_content=content,
            document_title=title,
            created_by_user_id=None,  # nullable FK; no real user owns ingest-script rules
            db=db,
            instructions=(
                "Focus on extracting concrete, actionable compliance rules that a "
                "marketing reviewer can evaluate against ad copy, brochures and "
                "social media posts. Skip background/explanatory passages."
            ),
            regulator=regulator,
        )

        # Tag every newly-created rule's metadata with the source filename so
        # _already_ingested() can recognise it on the next run.
        for r in result.get("rules", []):
            rule = db.query(Rule).filter(Rule.id == uuid.UUID(r["id"])).first()
            if rule:
                meta = dict(rule.rule_metadata or {})
                meta["source"] = source_name
                rule.rule_metadata = meta
                db.add(rule)
        db.commit()

        logger.info(
            f"    ✓ {result['rules_created']} rules · "
            f"{result['rules_failed']} failed · "
            f"{result['source_passages_indexed']} passages indexed"
        )
        return {
            "file": source_name,
            "skipped": False,
            "rules_created": result["rules_created"],
            "rules_failed": result["rules_failed"],
        }
    finally:
        db.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest guideline markdown files into rules + RAG")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip files whose rules are already in the DB (matched via metadata.source)",
    )
    parser.add_argument(
        "--dir",
        help="Override the guidelines directory (default: docs/guidelines-docs)",
    )
    args = parser.parse_args()

    guidelines_dir = Path(args.dir) if args.dir else _find_guidelines_dir()
    logger.info(f"Guidelines source: {guidelines_dir}")

    md_files = sorted(guidelines_dir.glob("*.md"))
    if not md_files:
        logger.warning(f"No .md files found in {guidelines_dir}")
        return

    logger.info(f"Found {len(md_files)} guideline file(s)")

    totals = {"rules_created": 0, "rules_failed": 0, "files_processed": 0, "files_skipped": 0}
    for path in md_files:
        try:
            r = await ingest_file(path, args.skip_existing)
            if r.get("skipped"):
                totals["files_skipped"] += 1
            else:
                totals["files_processed"] += 1
                totals["rules_created"] += r.get("rules_created", 0)
                totals["rules_failed"] += r.get("rules_failed", 0)
        except Exception as e:
            logger.error(f"  ✗ {path.name}: {e}")

    logger.info("=" * 60)
    logger.info(
        f"Done. Processed {totals['files_processed']} files "
        f"(skipped {totals['files_skipped']}) · "
        f"created {totals['rules_created']} rules "
        f"({totals['rules_failed']} failed)"
    )


if __name__ == "__main__":
    asyncio.run(main())
