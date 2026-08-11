"""Backfill product scope onto the corpora migration 0036 could not fill.

0036 adds `product_line` to the four product-bearing RAG tables and backfills
the two where a join is proof (rag_rules ← rules, rag_chunks ← submissions).
The remaining two derive their scope from files on disk, which is script work,
not migration work:

  rag_source_docs.product_line   ← guideline filename prefix, recovered from
                                   document_title (ingest_guidelines titles a
                                   doc as its title-cased filename stem)
  product_documents.product_type ← UIN → hand-curated fact card
                                   (backend/data/product_fact_cards/*.json)
  rag_product_docs.product_line  ← its product_documents row (typed above)

Inference is deliberately CONSERVATIVE: a row whose scope cannot be derived
from a known guideline prefix or a real fact card is left NULL and printed, so
an owner tags it. Guessing "global" for an unrecognised document would silently
widen what every future product filter retrieves.

`rag_compliance_examples` is out of scope: it carries no product signal at all.

Idempotent — only ever fills NULLs, so re-running after new ingests is safe.

Usage:
    python -m scripts.backfill_product_metadata            # dry run (report)
    python -m scripts.backfill_product_metadata --apply    # persist
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from typing import Any, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.services.rag.applicability import normalize_category  # noqa: E402
from scripts.ingest_guidelines import PRODUCT_LINE_BY_PREFIX  # noqa: E402

# force=True: importing ingest_guidelines (for the shared prefix table) already
# configured the root logger, and this script's output IS the deliverable.
logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
logger = logging.getLogger("backfill_product_metadata")


def infer_product_line_from_title(title: Optional[str]) -> Optional[str]:
    """Guideline document_title → product_line, or None when unrecognised.

    ingest_guidelines stores `path.stem.replace("_", " ").title()`, so
    `ulip_compliance_guidelines_part1.md` becomes "Ulip Compliance Guidelines
    Part1". Normalising back to the filename shape lets both spellings share
    ingest_guidelines.PRODUCT_LINE_BY_PREFIX — one mapping, one place.

    Unlike ingest (where an unmatched NEW guideline file is genuinely
    cross-product), an unmatched EXISTING title is just an unknown document:
    return None so it is reported instead of assumed global.
    """
    key = re.sub(r"[^a-z0-9]+", "_", (title or "").lower()).strip("_")
    if not key:
        return None
    for prefix, product_line in PRODUCT_LINE_BY_PREFIX:
        if key.startswith(prefix):
            return product_line
    return None


def product_line_for_uin(uin: Optional[str], fact_cards: Any) -> Optional[str]:
    """UIN → canonical product family from its fact card, None when unknown.

    Rider UINs resolve through their parent card (FactCardService.get), which is
    what we want for scoping: a rider brochure is `rider` scope either way.
    """
    card = fact_cards.get(uin) if uin else None
    return normalize_category((card or {}).get("product_category"))


def _report(label: str, filled: int, unresolved_rows: int, total: int) -> None:
    logger.info(
        f"  {label:<20} {total:>6} unscoped rows -> {filled:>6} inferable, "
        f"{unresolved_rows:>6} not inferable"
    )


def backfill_source_docs(db: Session, apply: bool) -> Tuple[int, List[str]]:
    """rag_source_docs.product_line from the guideline filename in the title."""
    groups = db.execute(
        text(
            "SELECT document_title, count(*) AS n FROM rag_source_docs "
            "WHERE product_line IS NULL GROUP BY document_title ORDER BY document_title"
        )
    ).all()

    filled = 0
    unresolved: List[str] = []
    unresolved_rows = 0
    for title, n in groups:
        product_line = infer_product_line_from_title(title)
        if product_line is None:
            unresolved.append(f"rag_source_docs   document_title={title!r} ({n} rows)")
            unresolved_rows += n
            continue
        filled += n
        if apply:
            db.execute(
                text(
                    "UPDATE rag_source_docs SET product_line = :pl "
                    "WHERE document_title = :title AND product_line IS NULL"
                ),
                {"pl": product_line, "title": title},
            )
    _report("rag_source_docs", filled, unresolved_rows, filled + unresolved_rows)
    return filled, unresolved


def backfill_product_documents(
    db: Session, apply: bool, fact_cards: Any
) -> Tuple[int, List[str]]:
    """product_documents.product_type from UIN → fact card.

    The column has existed since 0011 but ingest never wrote it (fixed in
    ingest_product_brochures for new documents; this fills the existing ones).
    """
    rows = db.execute(
        text(
            "SELECT id, uin, product_name FROM product_documents "
            "WHERE product_type IS NULL ORDER BY product_name"
        )
    ).all()

    filled = 0
    unresolved: List[str] = []
    for doc_id, uin, product_name in rows:
        product_type = product_line_for_uin(uin, fact_cards)
        if product_type is None:
            unresolved.append(
                f"product_documents uin={uin!r} {product_name!r} (id={doc_id})"
            )
            continue
        filled += 1
        if apply:
            db.execute(
                text(
                    "UPDATE product_documents SET product_type = :pt "
                    "WHERE id = :id AND product_type IS NULL"
                ),
                {"pt": product_type, "id": doc_id},
            )
    _report("product_documents", filled, len(unresolved), len(rows))
    return filled, unresolved


def backfill_rag_product_docs(
    db: Session, apply: bool, fact_cards: Any
) -> Tuple[int, List[str]]:
    """rag_product_docs.product_line from its product_documents row.

    Runs AFTER backfill_product_documents, but does not depend on it having
    committed: an untyped (or orphaned) row falls back to the same UIN → fact
    card lookup, so --apply and a dry run report the same numbers.
    """
    groups = db.execute(
        text(
            """
            SELECT r.product_document_id AS doc_id,
                   COALESCE(d.uin, r.uin) AS uin,
                   d.product_type AS product_type,
                   count(*) AS n
            FROM rag_product_docs r
            LEFT JOIN product_documents d ON d.id = r.product_document_id
            WHERE r.product_line IS NULL
            GROUP BY 1, 2, 3
            """
        )
    ).all()

    filled = 0
    unresolved: List[str] = []
    unresolved_rows = 0
    for doc_id, uin, product_type, n in groups:
        product_line = product_type or product_line_for_uin(uin, fact_cards)
        if product_line is None:
            unresolved.append(
                f"rag_product_docs  product_document_id={doc_id} uin={uin!r} ({n} rows)"
            )
            unresolved_rows += n
            continue
        filled += n
        if apply:
            db.execute(
                text(
                    "UPDATE rag_product_docs SET product_line = :pl "
                    "WHERE product_document_id = :doc_id AND product_line IS NULL"
                ),
                {"pl": product_line, "doc_id": doc_id},
            )
    _report("rag_product_docs", filled, unresolved_rows, filled + unresolved_rows)
    return filled, unresolved


def _migration_backfilled_status(db: Session) -> None:
    """Read-only: what migration 0036's joins left unscoped, and why.

    Nothing on disk can fix these — an unscoped rag_rules row means the RULE
    itself has no product_line (the applicability judge rejects those from every
    analysis), so they are tagged in the rules UI, not by this script.
    """
    for table, source in (
        ("rag_rules", "rules.product_line"),
        ("rag_chunks", "submissions.product_line"),
    ):
        n = db.execute(
            text(f"SELECT count(*) FROM {table} WHERE product_line IS NULL")  # noqa: S608
        ).scalar_one()
        logger.info(f"  {table:<20} {n:>6} unscoped rows -- untaggable here; fix {source}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="persist (default: dry run)")
    args = parser.parse_args()

    from app.services.fact_card_service import get_fact_card_service

    fact_cards = get_fact_card_service()
    if fact_cards.availability_issues:
        logger.error(
            "Fact cards unavailable (%s) — UIN scope cannot be inferred; aborting.",
            ", ".join(fact_cards.availability_issues),
        )
        return 1

    db: Session = SessionLocal()
    try:
        logger.info(
            "== product metadata backfill%s ==",
            "" if args.apply else " (dry run -- re-run with --apply to persist)",
        )
        unresolved: List[str] = []
        total = 0
        for filled, rows in (
            backfill_source_docs(db, args.apply),
            backfill_product_documents(db, args.apply, fact_cards),
            backfill_rag_product_docs(db, args.apply, fact_cards),
        ):
            total += filled
            unresolved.extend(rows)
        _migration_backfilled_status(db)

        if args.apply:
            db.commit()

        logger.info(
            "\n%s %d rows across 3 tables.",
            "Filled" if args.apply else "Would fill",
            total,
        )
        if unresolved:
            logger.info(
                "\nNo product could be inferred for the following -- tag these by hand:"
            )
            for line in unresolved:
                logger.info(f"  {line}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
