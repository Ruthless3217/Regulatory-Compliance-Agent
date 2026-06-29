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


async def _run(ledger: str, remediation: str, cache_dir: str, batch_size) -> None:
    svc = get_precedent_ingestion_service()
    summary = await svc.ingest(ledger, remediation, cache_dir, batch_size)
    logger.info("Ingestion summary:\n" + json.dumps(summary, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest precedent_cases from the ledger")
    p.add_argument("--ledger", default=None)
    p.add_argument("--remediation", default=None)
    p.add_argument("--cache-dir", default=None, dest="cache_dir")
    p.add_argument("--batch-size", type=int, default=None, dest="batch_size")
    args = p.parse_args()
    ledger, remediation, cache_dir = resolve_paths(args)
    if not os.path.exists(ledger):
        raise SystemExit(f"Ledger not found: {ledger}")
    asyncio.run(_run(ledger, remediation, cache_dir, args.batch_size))


if __name__ == "__main__":
    main()
