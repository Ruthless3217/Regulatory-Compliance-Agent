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
    """Return (precision, recall) for set-based matching.

    Conventions:
    - Both empty → (0.0, 0.0): no signal in either direction.
    - ``predicted`` empty, ``actual`` non-empty → precision=0.0, recall=0.0:
      the model produced nothing, so it missed everything.
    - ``predicted`` non-empty, ``actual`` empty → precision=0.0, recall=1.0:
      vacuous recall (nothing to recall, so recall is vacuously satisfied);
      precision=0.0 already signals that every prediction is a false positive,
      so over-prediction is still caught at the precision dimension.
      Aggregate recall is computed once over the union of all eval ground-truth
      sets, so this branch is only reached when the *entire* eval split has
      zero aligned ground-truth comments — a degenerate run that should be
      investigated independently.
    """
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

async def _run(folder: str, eval_frac: float, max_docs: int | None = None, top_k: int | None = None) -> Dict:
    from app.services.knowledge_base_ingestion import (
        get_kb_ingestion_service,
        parse_compliance_comments,
        classify_severity,
        align_comment_to_chunk,
    )
    from app.services.rag.retrievers.precedent_retriever import get_precedent_retriever
    from app.services.preprocessing_service import ContextEngineeringService
    from app.services.llm_service import llm_service
    from app.schemas.compliance_schemas import PrecedentCitationsResult
    from app.services.rag.factory import get_embedder
    from app.config import settings

    files = sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".json"))
    train, ev = split_by_hash(files, eval_frac)
    logger.info(f"Split: {len(train)} train / {len(ev)} eval")

    svc = get_kb_ingestion_service()
    # Ingest only the train split. Dedup against the live KB so re-runs (and
    # KBs that already contain the full corpus) skip the embedding work.
    # The same-document_id exclusion at retrieval still prevents leakage even
    # when the eval-split rows pre-exist in the KB.
    inserted = 0
    skipped = 0
    batch: List[Dict] = []
    from app.services.rag.indexers.compliance_examples_indexer import upsert_examples

    async def _flush(rows: List[Dict]) -> int:
        nonlocal skipped
        if not rows:
            return 0
        existing = await svc._existing_ids_async([r["id"] for r in rows])
        fresh = [r for r in rows if r["id"] not in existing]
        skipped += len(rows) - len(fresh)
        if not fresh:
            return 0
        try:
            return await upsert_examples(fresh)
        except Exception as e:
            logger.warning(f"train flush failed ({len(fresh)} rows): {e}")
            return 0

    for path in train:
        try:
            rows = svc.parse_file(path)["rows"]
        except Exception as e:
            logger.warning(f"train parse skip {os.path.basename(path)}: {e}")
            continue
        batch.extend(rows)
        if len(batch) >= settings.kb_batch_size:
            inserted += await _flush(batch)
            batch = []
    if batch:
        inserted += await _flush(batch)
    logger.info(f"Ingested {inserted} train precedents (skipped {skipped} duplicates)")

    retriever = get_precedent_retriever()
    embedder = get_embedder()
    ctx = ContextEngineeringService(db=None)
    k = top_k or settings.pgvector_top_k

    docs_evaluated = 0
    gen_total = real_total = 0
    novel_total = 0  # novel findings emitted across all chunks (no precedent)
    micro_pred: Set[str] = set()
    micro_actual: Set[str] = set()
    missed_criticals = 0
    sims: List[float] = []
    # Phase 1.5 metrics: did we cite WHAT the reviewer marked (anchor-level recall)?
    anchor_hits = 0
    anchor_total = 0
    distinct_precedents_cited: Set[str] = set()
    # Quota / failure detection: if N consecutive LLM calls return an empty
    # PrecedentCitationsResult, assume the provider is throttling us out and
    # bail rather than burn the rest of the run on fallbacks.
    consec_empty_calls = 0
    EMPTY_BAIL_THRESHOLD = 25
    quota_exhausted = False

    if max_docs is not None:
        ev = ev[:max_docs]
        logger.info(f"--max-eval-docs limits run to first {len(ev)} eval docs")

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

    def _checkpoint(extra: Dict | None = None) -> None:
        precision, recall = precision_recall(micro_pred, micro_actual)
        m = {
            "docs_evaluated": docs_evaluated,
            "train_files": len(train),
            "eval_files": len(ev),
            "precision_presence": round(precision, 4),
            "recall_presence": round(recall, 4),
            "missed_criticals": missed_criticals,
            "anchor_recall": round(anchor_hits / anchor_total, 4) if anchor_total else None,
            "anchor_hits": anchor_hits,
            "anchor_total": anchor_total,
            "distinct_precedents_cited": len(distinct_precedents_cited),
            "mean_comment_cosine": round(sum(sims) / len(sims), 4) if sims else None,
            "generated_violations": gen_total,
            "novel_findings": novel_total,
            "real_violations": real_total,
            "quota_exhausted": quota_exhausted,
            "top_k": k,
        }
        if extra:
            m.update(extra)
        os.makedirs("logs", exist_ok=True)
        with open(os.path.join("logs", "eval_replay.json"), "w", encoding="utf-8") as f:
            json.dump(m, f, indent=2)
        return m

    for path in ev:
        if quota_exhausted:
            break
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
            chunk_objs, top_k=k, exclude_document_id=document_id
        )
        gen_chunks: Set[int] = set()
        gen_pairs: List[Tuple[int, Dict]] = []  # (chunk_idx, precedent_dict cited)
        for cobj in chunk_objs:
            preds = retrieved.get(cobj["id"], [])
            # No longer skip empty-precedent chunks: novel-only mode lets the
            # model flag corpus-uncovered issues (2026-05-28 design).
            prompt = ctx.create_precedent_prompts(cobj["text"], preds)
            try:
                result = await llm_service.generate_structured_response(
                    prompt=prompt, output_model=PrecedentCitationsResult,
                    system_prompt="Cite which historical precedents apply. JSON only.",
                    temperature=0.0,
                )
            except Exception as e:
                logger.warning(f"LLM call failed for chunk {cobj['id']}: {e}")
                consec_empty_calls += 1
                if consec_empty_calls >= EMPTY_BAIL_THRESHOLD:
                    logger.error(
                        f"{EMPTY_BAIL_THRESHOLD} consecutive failed LLM calls — "
                        "assuming provider quota exhausted; bailing out early."
                    )
                    quota_exhausted = True
                    break
                continue

            citations = list(result.citations or [])
            novel = list(result.novel_findings or [])
            if not citations and not novel:
                consec_empty_calls += 1
                if consec_empty_calls >= EMPTY_BAIL_THRESHOLD:
                    logger.error(
                        f"{EMPTY_BAIL_THRESHOLD} consecutive empty results — "
                        "assuming provider quota exhausted; bailing out early."
                    )
                    quota_exhausted = True
                    break
                continue
            consec_empty_calls = 0  # got a real response

            # A chunk counts as "generated" if it produced a citation OR a
            # novel finding — both surface as a flag the reviewer would see.
            gen_chunks.add(cobj["chunk_index"])
            novel_total += len(novel)
            for c in citations:
                idx = int(c.precedent_index)
                if not (0 <= idx < len(preds)):
                    continue
                gen_pairs.append((cobj["chunk_index"], preds[idx]))
                if preds[idx].get("id"):
                    distinct_precedents_cited.add(str(preds[idx]["id"]))

        gen_total += len(gen_pairs)
        real_total += len(real)
        # Micro presence sets keyed by (doc, chunk_idx).
        micro_pred |= {f"{document_id}:{ci}" for ci in gen_chunks}
        micro_actual |= {f"{document_id}:{ci}" for ci in real_chunks}
        matched_anchors = {r["anchor"] for r in real if r["chunk_idx"] in gen_chunks}
        missed_criticals += count_missed_criticals(real, matched_anchors)

        # Anchor-level recall: do the precedent anchors we cited overlap with
        # the anchors the real reviewer marked on this doc? Substring match in
        # either direction (anchors are often partial highlights of a phrase).
        cited_anchors = {(p.get("anchor_text") or "").strip().lower() for _, p in gen_pairs}
        cited_anchors.discard("")
        for r in real:
            if r["chunk_idx"] is None:
                continue
            anchor_total += 1
            ra = (r["anchor"] or "").strip().lower()
            if not ra:
                continue
            if any(ra in ca or ca in ra for ca in cited_anchors):
                anchor_hits += 1

        # Cosine similarity: precedent comment_text (carried verbatim into the
        # output) vs. the real reviewer comment on the same chunk.
        for ci, p in gen_pairs:
            same = [r["comment"] for r in real if r["chunk_idx"] == ci]
            cite_text = (p.get("comment_text") or "").strip()
            if not same or not cite_text:
                continue
            try:
                embs = await embedder.embed([cite_text, same[0]])
                sims.append(_cos(embs[0], embs[1]))
            except Exception:
                pass
        docs_evaluated += 1
        _checkpoint()  # per-doc checkpoint so a kill doesn't lose progress

    metrics = _checkpoint()
    logger.info("Eval metrics:\n" + json.dumps(metrics, indent=2))
    return metrics


def main() -> None:
    p = argparse.ArgumentParser(description="Leakage-safe precedent replay eval")
    p.add_argument("--folder", required=True)
    p.add_argument("--eval-frac", type=float, default=0.1)
    p.add_argument(
        "--max-eval-docs",
        type=int,
        default=None,
        help="Cap the number of eval docs processed (useful for fitting within LLM-provider daily quotas).",
    )
    p.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Override the per-chunk precedent retrieval depth (default: settings.pgvector_top_k).",
    )
    args = p.parse_args()
    asyncio.run(_run(args.folder, args.eval_frac, args.max_eval_docs, args.top_k))


if __name__ == "__main__":
    main()
