"""Batch-ingest product brochures into the product reference corpus.

For every PDF in the given directory:
  1. sha256 hash — already-ingested files are skipped (idempotent re-runs)
  2. zero-token structural parse (brochure_parser: sections, tables, UIN)
  3. fail-closed validation — suspicious parses are persisted as
     status='quarantined' with explicit reasons (NEVER silently ingested or
     skipped; a mangled brochure would poison product retrieval)
  4. clean parses persist ProductDocument + ProductTable rows and embed
     section chunks + table summaries into rag_product_docs

Usage:
    python -m scripts.ingest_product_brochures <dir-or-pdf>           # dry run
    python -m scripts.ingest_product_brochures <dir-or-pdf> --apply   # persist
"""
import argparse
import asyncio
import hashlib
import logging
import sys
import uuid
from pathlib import Path
from typing import List

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

#: Below this, the parse almost certainly failed (scanned/odd encoding).
MIN_TEXT_CHARS = 500
#: A real brochure has structure; fewer sections = heading detection failed.
MIN_SECTIONS = 3


def validate_brochure(parsed) -> List[str]:
    """Fail-closed gate: return [] for a clean parse, else quarantine reasons."""
    reasons: List[str] = []
    if len(parsed.full_text or "") < MIN_TEXT_CHARS:
        reasons.append(
            f"insufficient text extracted ({len(parsed.full_text or '')} chars "
            f"< {MIN_TEXT_CHARS}) — scanned PDF or extraction failure?"
        )
    if len(parsed.sections) < MIN_SECTIONS:
        reasons.append(
            f"only {len(parsed.sections)} sections found (< {MIN_SECTIONS}) — "
            f"heading detection likely failed for this layout"
        )
    if not parsed.uin:
        found = list(getattr(parsed, "uins", None) or [])
        if found:
            # UINs were found but none is locally tied to the descriptor or the
            # product name (brochure_parser.select_primary_uin). The old rule
            # took the first one in text order and stamped a rider onto 14 of
            # 49 documents; quarantining for a human is the correct answer.
            reasons.append(
                f"{len(found)} UIN(s) found {found} but none is on the regulatory "
                f"descriptor line or a product-name line "
                f"({getattr(parsed, 'uin_selection_reason', 'role_unresolved')}) "
                f"— primary UIN left unresolved rather than guessed"
            )
        else:
            reasons.append("no UIN found — every IRDAI-approved product document carries one")
    if not parsed.product_name:
        reasons.append("no product name detected (no running header?)")
    return reasons


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


async def _ingest_one(path: Path, apply: bool, db) -> str:
    """Returns one of: ingested | quarantined | skipped."""
    from app.models.product_document import ProductDocument, ProductTable
    from app.services.brochure_parser import parse_brochure
    from app.services.fact_card_service import get_fact_card_service
    from scripts.backfill_product_metadata import product_line_for_uin

    digest = _sha256(path)
    existing = (
        db.query(ProductDocument).filter(ProductDocument.file_hash == digest).first()
    )
    if existing is not None:
        logger.info(f"SKIP (already ingested as {existing.status}): {path.name}")
        return "skipped"

    parsed = parse_brochure(str(path))
    reasons = validate_brochure(parsed)
    status = "quarantined" if reasons else "ingested"
    # Product family from the hand-curated fact card. NULL when the UIN has no
    # card — an unscoped document is visible for curation, never guessed.
    product_type = product_line_for_uin(parsed.uin, get_fact_card_service())

    logger.info(
        f"{'WOULD ' if not apply else ''}{status.upper()}: {path.name} "
        f"[{parsed.product_name or '?'} / {parsed.uin or 'no-UIN'} / "
        f"{product_type or 'unscoped'}] "
        f"{len(parsed.sections)} sections, {len(parsed.tables)} tables"
    )
    for r in reasons:
        logger.info(f"    reason: {r}")

    if not apply:
        return status

    doc = ProductDocument(
        id=uuid.uuid4(),
        product_name=parsed.product_name or path.stem,
        product_type=product_type,
        uin=parsed.uin,
        uins=parsed.uins,
        descriptor=parsed.descriptor,
        source_file=str(path),
        file_hash=digest,
        page_count=parsed.page_count,
        section_count=len(parsed.sections),
        table_count=len(parsed.tables),
        body_font_size=parsed.body_font_size,
        status=status,
        quarantine_reasons=reasons or None,
    )
    db.add(doc)
    if status == "ingested":
        for t in parsed.tables:
            db.add(ProductTable(
                id=uuid.uuid4(),
                product_document_id=doc.id,
                page_number=t.page_number,
                table_index=t.table_index,
                heading=t.heading,
                rows=t.rows,
                summary=t.summary,
            ))

    # Index BEFORE committing the registry row: if embedding/upsert fails the
    # rollback leaves no trace and the next run retries this file. Committing
    # first would strand a doc marked 'ingested' with zero vectors — a silent
    # retrieval hole that hash-idempotency would then protect forever.
    if status == "ingested":
        from app.services.rag.indexers.product_docs_indexer import index_product_document
        db.flush()  # assign doc.id without ending the transaction
        n = await index_product_document(doc.id, parsed, product_line=product_type)
        logger.info(f"    indexed {n} vectors into rag_product_docs")
    db.commit()
    return status


try:
    from langsmith import traceable
except ImportError:
    def traceable(*_a, **_kw):
        return lambda f: f

@traceable(run_type="chain", name="Ingest Product Brochures")
async def _run(target: Path, apply: bool) -> int:
    from app.database import SessionLocal

    pdfs = [target] if target.is_file() else sorted(target.glob("**/*.pdf"))
    if not pdfs:
        logger.error(f"No PDFs found under {target}")
        return 1

    counts = {"ingested": 0, "quarantined": 0, "skipped": 0, "error": 0}
    db = SessionLocal()
    try:
        for path in pdfs:
            try:
                counts[await _ingest_one(path, apply, db)] += 1
            except Exception as e:
                db.rollback()
                counts["error"] += 1
                logger.error(f"ERROR: {path.name}: {e}")
    finally:
        db.close()

    mode = "" if apply else " (dry run — re-run with --apply)"
    logger.info(
        f"\nDone{mode}: {counts['ingested']} ingested, "
        f"{counts['quarantined']} quarantined, {counts['skipped']} skipped, "
        f"{counts['error']} errors of {len(pdfs)} PDFs."
    )
    # Quarantines and errors are visible, not fatal: the batch reports them
    # for manual review (fail closed on the document, not the whole run).
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="directory of PDFs (recursive) or a single PDF")
    parser.add_argument("--apply", action="store_true", help="persist (default: dry run)")
    args = parser.parse_args()
    return asyncio.run(_run(Path(args.target), args.apply))


if __name__ == "__main__":
    sys.exit(main())
