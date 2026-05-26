"""Leakage-safe replay eval for the precedent analysis path.

Deterministically split the _rl corpus by hash(source_file) into train/eval.
Ingest only train into a fresh KB; evaluate on eval. No eval doc's own
precedents can be retrieved (retrieval also excludes same document_id).

Pure metric helpers are unit-tested; the live run needs DB + LLM + embedder.

Usage:
  cd backend && python -m scripts.eval_precedent_replay --folder ../dataset/Dataset/Dataset/dataset_2.1_rl --eval-frac 0.1
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from typing import Dict, List, Set, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("eval_precedent_replay")


# ----------------------------- pure helpers -----------------------------

def split_by_hash(files: List[str], eval_frac: float = 0.1) -> Tuple[List[str], List[str]]:
    """Deterministic split: a file is 'eval' if md5(name) mod 1000 < eval_frac*1000."""
    cutoff = int(eval_frac * 1000)
    train, ev = [], []
    for f in files:
        h = int(hashlib.md5(os.path.basename(f).encode("utf-8")).hexdigest(), 16) % 1000
        (ev if h < cutoff else train).append(f)
    return train, ev


def precision_recall(predicted: Set[str], actual: Set[str]) -> Tuple[float, float]:
    if not predicted and not actual:
        return 0.0, 0.0
    tp = len(predicted & actual)
    precision = tp / len(predicted) if predicted else 0.0
    recall = (tp / len(actual)) if actual else 1.0
    return precision, recall


def count_missed_criticals(real_comments: List[Dict], matched_anchors: Set[str]) -> int:
    return sum(
        1
        for c in real_comments
        if c.get("severity") == "critical" and c.get("anchor") not in matched_anchors
    )


# ----------------------------- live run -----------------------------

async def _run(folder: str, eval_frac: float) -> Dict:
    from app.services.knowledge_base_ingestion import (
        get_kb_ingestion_service,
        parse_compliance_comments,
        classify_severity,
        align_comment_to_chunk,
    )
    from app.services.rag.retrievers.precedent_retriever import get_precedent_retriever
    from app.services.preprocessing_service import ContextEngineeringService
    from app.services.llm_service import llm_service
    from app.schemas.compliance_schemas import ComplianceAnalysisResult
    from app.services.rag.factory import get_embedder
    from app.config import settings

    files = sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".json"))
    train, ev = split_by_hash(files, eval_frac)
    logger.info(f"Split: {len(train)} train / {len(ev)} eval")

    svc = get_kb_ingestion_service()
    # Ingest only the train split (caller is expected to start from a fresh KB).
    inserted = 0
    batch: List[Dict] = []
    from app.services.rag.indexers.compliance_examples_indexer import upsert_examples
    for path in train:
        try:
            batch.extend(svc.parse_file(path)["rows"])
            if len(batch) >= settings.kb_batch_size:
                inserted += await upsert_examples(batch); batch = []
        except Exception as e:
            logger.warning(f"train ingest skip {os.path.basename(path)}: {e}")
    inserted += await upsert_examples(batch)
    logger.info(f"Ingested {inserted} train precedents")

    retriever = get_precedent_retriever()
    embedder = get_embedder()
    ctx = ContextEngineeringService(db=None)

    docs_evaluated = 0
    gen_total = real_total = 0
    micro_pred: Set[str] = set()
    micro_actual: Set[str] = set()
    missed_criticals = 0
    sims: List[float] = []

    def _chunk(content: str) -> List[str]:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        sp = RecursiveCharacterTextSplitter(
            chunk_size=settings.kb_chunk_size, chunk_overlap=settings.kb_chunk_overlap
        )
        return [c for c in sp.split_text(content or "") if c.strip()]

    def _cos(a, b) -> float:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    for path in ev:
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        inp = data.get("input") or {}
        meta = data.get("metadata") or {}
        draft = inp.get("draft_text") or ""
        if not draft.strip():
            continue
        document_id = str(meta.get("document_id") or "")
        chunks = _chunk(draft)
        chunk_objs = [{"id": f"c{i}", "text": t, "chunk_index": i} for i, t in enumerate(chunks)]

        # Ground truth from the real comments.
        real = []
        for pc in parse_compliance_comments(inp.get("compliance_comments"), os.path.basename(path)):
            idx, _ = align_comment_to_chunk(pc.anchor, chunks, settings.kb_min_fuzzy_score)
            real.append({"anchor": pc.anchor, "severity": classify_severity(pc.reviewer),
                         "chunk_idx": idx, "comment": pc.comment})
        real_anchors = {r["anchor"] for r in real if r["chunk_idx"] is not None}
        real_chunks = {r["chunk_idx"] for r in real if r["chunk_idx"] is not None}

        # Generated, with same-document leakage excluded.
        retrieved = await retriever.retrieve_per_chunk(
            chunk_objs, top_k=settings.pgvector_top_k, exclude_document_id=document_id
        )
        gen_chunks: Set[int] = set()
        gen_descriptions: List[Tuple[int, str]] = []
        for cobj in chunk_objs:
            preds = retrieved.get(cobj["id"], [])
            if not preds:
                continue
            prompt = ctx.create_precedent_prompts(cobj["text"], preds)
            result = await llm_service.generate_structured_response(
                prompt=prompt, output_model=ComplianceAnalysisResult,
                system_prompt="Imitate the example reviewers. JSON only.", temperature=0.0,
            )
            if result.violations:
                gen_chunks.add(cobj["chunk_index"])
                for v in result.violations:
                    gen_descriptions.append((cobj["chunk_index"], v.description or ""))

        gen_total += len(gen_descriptions)
        real_total += len(real)
        # Micro presence sets keyed by (doc, chunk_idx).
        micro_pred |= {f"{document_id}:{ci}" for ci in gen_chunks}
        micro_actual |= {f"{document_id}:{ci}" for ci in real_chunks}
        matched_anchors = {r["anchor"] for r in real if r["chunk_idx"] in gen_chunks}
        missed_criticals += count_missed_criticals(real, matched_anchors)

        # Comment cosine: match generated descriptions to real comments on the same chunk.
        for ci, desc in gen_descriptions:
            same = [r["comment"] for r in real if r["chunk_idx"] == ci]
            if not same or not desc.strip():
                continue
            try:
                embs = await embedder.embed([desc, same[0]])
                sims.append(_cos(embs[0], embs[1]))
            except Exception:
                pass
        docs_evaluated += 1

    precision, recall = precision_recall(micro_pred, micro_actual)
    metrics = {
        "docs_evaluated": docs_evaluated,
        "train_files": len(train),
        "eval_files": len(ev),
        "precision_presence": round(precision, 4),
        "recall_presence": round(recall, 4),
        "missed_criticals": missed_criticals,
        "mean_comment_cosine": round(sum(sims) / len(sims), 4) if sims else None,
        "generated_violations": gen_total,
        "real_violations": real_total,
    }
    os.makedirs("logs", exist_ok=True)
    with open(os.path.join("logs", "eval_replay.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    logger.info("Eval metrics:\n" + json.dumps(metrics, indent=2))
    return metrics


def main() -> None:
    p = argparse.ArgumentParser(description="Leakage-safe precedent replay eval")
    p.add_argument("--folder", required=True)
    p.add_argument("--eval-frac", type=float, default=0.1)
    args = p.parse_args()
    asyncio.run(_run(args.folder, args.eval_frac))


if __name__ == "__main__":
    main()
