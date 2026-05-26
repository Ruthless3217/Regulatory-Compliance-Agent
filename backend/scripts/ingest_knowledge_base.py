"""Ingest the precedent knowledge base (dataset_2.1_rl JSON) into rag_compliance_examples.

Usage:
  # In the backend container (after the additive ro mount in docker-compose):
  docker exec compliance-backend python -m scripts.ingest_knowledge_base
  docker exec compliance-backend python -m scripts.ingest_knowledge_base --limit 10 --preview

  # On the host (Postgres exposed on localhost:5432; embedder env must be set):
  cd backend && python -m scripts.ingest_knowledge_base --folder ../dataset/Dataset/Dataset/dataset_2.1_rl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.knowledge_base_ingestion import get_kb_ingestion_service  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ingest_knowledge_base")

# Container mount first, then host-relative fallbacks.
DEFAULT_FOLDERS = [
    "/app/dataset/Dataset/Dataset/dataset_2.1_rl",
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "dataset", "Dataset", "Dataset", "dataset_2.1_rl")
    ),
]


def _resolve_folder(arg: str | None) -> str:
    if arg:
        return arg
    for cand in DEFAULT_FOLDERS:
        if os.path.isdir(cand):
            return cand
    raise FileNotFoundError(
        "dataset_2.1_rl not found. Pass --folder. Tried: " + ", ".join(DEFAULT_FOLDERS)
    )


def _preview(folder: str) -> None:
    svc = get_kb_ingestion_service()
    files = sorted(f for f in os.listdir(folder) if f.endswith(".json"))
    if not files:
        print(f"No JSON files in {folder}")
        return
    path = os.path.join(folder, files[0])
    parsed = svc.parse_file(path)
    print(f"Preview of {files[0]}:")
    print(f"  document_id={parsed['document_id']}  rows={len(parsed['rows'])}  unmatched={parsed['unmatched']}")
    for r in parsed["rows"][:8]:
        print("  ---")
        print(f"  reviewer : {r['reviewer_name']}")
        print(f"  severity : {r['severity']}  category: {r['violation_category']}")
        print(f"  comment  : {r['comment_text'][:100]}")
        print(f"  chunk    : {r['chunk_text'][:100]}")
    try:
        input("\nPress Enter to exit preview (no data written)...")
    except EOFError:
        pass


async def _run(folder: str, limit: int | None) -> None:
    svc = get_kb_ingestion_service()
    logger.info(f"Ingesting from {folder} (limit={limit})")
    summary = await svc.ingest_folder(folder, limit=limit)
    logger.info("=" * 60)
    logger.info("Ingestion summary:\n" + json.dumps(summary, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest precedent knowledge base")
    p.add_argument("--folder", help="Path to dataset_2.1_rl (auto-resolved if omitted)")
    p.add_argument("--limit", type=int, default=None, help="Process only the first N files")
    p.add_argument("--preview", action="store_true", help="Parse one file, print pairings, exit")
    args = p.parse_args()

    folder = _resolve_folder(args.folder)
    if args.preview:
        _preview(folder)
        return
    asyncio.run(_run(folder, args.limit))


if __name__ == "__main__":
    main()
