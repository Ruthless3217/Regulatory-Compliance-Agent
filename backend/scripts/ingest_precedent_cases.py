"""Ingest docs/ledger into precedent_cases.

Usage (host, DB env set, after `alembic upgrade head`):
  cd backend && python -m scripts.ingest_precedent_cases \
      --ledger ../docs/ledger/comment_ledger.jsonl \
      --remediation ../docs/ledger/remediation_pairs.jsonl \
      --cache-dir ./data/precedent_enrich_cache
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.precedent_ingestion import get_precedent_ingestion_service  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest_precedent_cases")

_DEFAULT_LEDGER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "ledger", "comment_ledger.jsonl"))
_DEFAULT_REM = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "ledger", "remediation_pairs.jsonl"))
_DEFAULT_CACHE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "precedent_enrich_cache"))


def resolve_paths(args) -> Tuple[str, str, str]:
    return (
        args.ledger or _DEFAULT_LEDGER,
        args.remediation or _DEFAULT_REM,
        args.cache_dir or _DEFAULT_CACHE,
    )


try:
    from langsmith import traceable
except ImportError:
    def traceable(*_a, **_kw):
        return lambda f: f

def _precedent_ids(db) -> set:
    """Every precedent id currently in the table. precedent_cases has no ORM
    model (it is managed as a raw pgvector table), hence the text query."""
    from sqlalchemy import text

    return {str(r[0]) for r in db.execute(text("SELECT id FROM precedent_cases")).fetchall()}


def _claim_new_rows(layer_name: str, before: set) -> None:
    """Assign rows this ingest actually created to a fresh corpus layer, so the
    contribution can later be disabled or purged as one unit.

    Membership is an exact before/after id diff rather than a source_file match:
    one ingest spans many source files, and matching by name would also sweep up
    the pre-existing layer-less rows that predate layers entirely.
    """
    from sqlalchemy import text

    from app.database import SessionLocal
    from app.services.corpus_layer_service import CorpusLayerError, create_layer

    db = SessionLocal()
    try:
        new_ids = sorted(_precedent_ids(db) - before)
        if not new_ids:
            logger.info("Layer %r: ingest added no new precedents; layer not created.", layer_name)
            return
        try:
            layer = create_layer(
                db,
                name=layer_name,
                kind="precedent_ingest",
                description=f"Precedent ingest of {len(new_ids)} rows.",
                claim=False,  # we assign by explicit id diff below, not by source_ref
            )
        except CorpusLayerError as e:
            logger.error("Layer %r not created (%s). Rows remain unlayered.", layer_name, e)
            return

        db.execute(
            text(
                "UPDATE precedent_cases SET source_layer_id = :lid"
                " WHERE id = ANY(:ids) AND source_layer_id IS NULL"
            ),
            {"lid": layer["id"], "ids": new_ids},
        )
        db.commit()
        logger.info("Layer %r (%s): claimed %d new precedents.", layer_name, layer["id"], len(new_ids))
    finally:
        db.close()


@traceable(run_type="chain", name="Ingest Precedent Cases")
async def _run(ledger: str, remediation: str, cache_dir: str, batch_size, layer: str = None) -> None:
    before: set = set()
    if layer:
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            before = _precedent_ids(db)
        finally:
            db.close()

    svc = get_precedent_ingestion_service()
    summary = await svc.ingest(ledger, remediation, cache_dir, batch_size)
    logger.info("Ingestion summary:\n" + json.dumps(summary, indent=2))

    if layer:
        _claim_new_rows(layer, before)


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest precedent_cases from the ledger")
    p.add_argument("--ledger", default=None)
    p.add_argument("--remediation", default=None)
    p.add_argument("--cache-dir", default=None, dest="cache_dir")
    p.add_argument("--batch-size", type=int, default=None, dest="batch_size")
    p.add_argument(
        "--layer",
        default=None,
        help="Register this ingest as a named corpus layer (migration 0030) and "
             "claim the rows it creates, so an admin can later disable or purge "
             "this contribution as one unit. Omit to ingest unlayered (legacy).",
    )
    args = p.parse_args()
    ledger, remediation, cache_dir = resolve_paths(args)
    if not os.path.exists(ledger):
        raise SystemExit(f"Ledger not found: {ledger}")
    asyncio.run(_run(ledger, remediation, cache_dir, args.batch_size, args.layer))


if __name__ == "__main__":
    main()
