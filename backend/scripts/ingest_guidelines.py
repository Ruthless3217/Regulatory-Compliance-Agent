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

Filename prefix → product_line (the scope stamped on every rule from the file;
generate_rules_from_text REQUIRES it — omitting it made this script a silent
no-op from 2026-08-03) and → regulator (source-doc tagging):

  prefix                    product_line      regulator
  ulip_*                    ulip              irdai
  term_insurance_*          term              irdai
  retirement_and_pension_*  pension_annuity   irdai
  tax_and_gst_*             global            regulatory
  disclaimer*               global            regulatory
  (anything else)           global            regulatory
  *sebi* anywhere in name   (as above)        sebi

Usage:
  docker exec compliance-backend python -m scripts.ingest_guidelines
  docker exec compliance-backend python -m scripts.ingest_guidelines --skip-existing

Exit code is non-zero if any file failed, including a file that produced zero
rules while reporting errors — that combination WAS the silent-success bug.
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


#: Guideline filename prefix → product_line, in match order. Values are from
#: rule_generator_service._ALLOWED_PRODUCT_LINES; anything unmatched is
#: cross-product and ingests as "global". Shared with
#: scripts.backfill_product_metadata (which reports unmatched instead of
#: defaulting, because an arbitrarily-titled uploaded doc is not evidence of
#: global scope).
PRODUCT_LINE_BY_PREFIX = (
    ("ulip", "ulip"),
    ("term_insurance", "term"),
    ("retirement_and_pension", "pension_annuity"),
    ("tax_and_gst", "global"),
    ("disclaimer", "global"),
)


def _product_line_for(filename: str) -> str:
    name = filename.lower()
    for prefix, product_line in PRODUCT_LINE_BY_PREFIX:
        if name.startswith(prefix):
            return product_line
    return "global"


def _regulator_for(filename: str) -> str:
    """ULIP MARKETING compliance is IRDAI's, not SEBI's — these guideline docs
    are IRDAI advertising rules for a unit-linked product, so only a filename
    that genuinely names SEBI is tagged sebi."""
    name = filename.lower()
    if "sebi" in name:
        return "sebi"
    if name.startswith(("retirement_and_pension", "term_insurance", "ulip")):
        return "irdai"
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
    product_line = _product_line_for(path.name)

    db = SessionLocal()
    try:
        if skip_existing and _already_ingested(db, source_name):
            logger.info(f"  ⤵ skip (already ingested): {source_name}")
            return {"file": source_name, "skipped": True, "rules_created": 0}

        content = path.read_text(encoding="utf-8", errors="replace")
        logger.info(
            f"  → {source_name} ({len(content)} chars, regulator={regulator}, "
            f"product_line={product_line})"
        )

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
            product_line=product_line,
        )

        # Fail loudly. generate_rules_from_text reports its failures in
        # result["errors"] and returns normally, so "0 rules + errors" used to
        # print a ✓ line and exit 0 (the whole corpus silently stopped being
        # ingested for a month). Nothing but a raise is allowed here.
        if not result.get("rules_created") and result.get("errors"):
            raise RuntimeError(
                f"{source_name}: 0 rules created — " + "; ".join(result["errors"])
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


try:
    from langsmith import traceable
except ImportError:
    def traceable(*_a, **_kw):
        return lambda f: f

@traceable(run_type="chain", name="Ingest Guidelines")
async def main() -> int:
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
        logger.error(f"No .md files found in {guidelines_dir}")
        return 1

    logger.info(f"Found {len(md_files)} guideline file(s)")

    totals = {
        "rules_created": 0, "rules_failed": 0,
        "files_processed": 0, "files_skipped": 0, "files_errored": 0,
    }
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
            totals["files_errored"] += 1
            logger.error(f"  ✗ {path.name}: {e}")

    logger.info("=" * 60)
    logger.info(
        f"Done. Processed {totals['files_processed']} files "
        f"(skipped {totals['files_skipped']}, errored {totals['files_errored']}) · "
        f"created {totals['rules_created']} rules "
        f"({totals['rules_failed']} failed)"
    )
    if totals["files_errored"]:
        logger.error(
            f"INGEST FAILED: {totals['files_errored']} of {len(md_files)} files "
            f"produced no rules or raised. The rule corpus is NOT up to date."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
