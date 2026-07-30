"""Build canonical precedent_cases rows from the clean comment ledger.

Pipeline: filter substantive reviewer comments -> recover product category ->
link approved rewrite from remediation pairs -> reattach reply threads ->
LLM-enrich (cached) -> dedup by canonical hash (rollup occurrence_count +
example_tickets) -> embed + index.

Re-ingest idempotency: each row's `id` is precedent_id(canonical_hash(issue_type,
span, comment)) and the DB upsert is ON CONFLICT(id). The canonical hash is built
from the LLM-generated `issue_type`, so stable ids across re-ingest depend on
`issue_type` being reproducible. This is guaranteed only when `cache_dir`
PERSISTS between runs: the enrich cache returns the prior issue_type byte-for-byte
on a hit. Wiping cache_dir (or feeding a logically-identical issue with a
different seed/span/comment) can re-derive a different issue_type, yielding a new
id and a duplicate DB row. Treat cache_dir persistence as a hard requirement.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.precedent.dedup import canonical_hash, precedent_id
from app.services.precedent.enrichment import enrich
from app.services.precedent.remediation import link_remediation
from app.services.rag.precedent_filters import is_thin_comment

logger = logging.getLogger(__name__)

# Map a topic/title hint to a product family (best-effort; null when unknown).
# Non-Par must be checked before Par: "non-participating" contains "participating"
# as a substring, so the more specific needle has to win the first-match order.
_PRODUCT_HINTS = [
    ("ULIP", ("ulip", "unit linked", "market linked")),
    ("Term", ("term insurance", "term plan", "protection")),
    ("Pension", ("pension", "retirement", "annuity")),
    ("Child", ("child", "education")),
    ("Savings", ("savings", "endowment", "guaranteed")),
    ("Non-Par", ("non-par", "non par", "nonpar", "non-participating", "non participating")),
    ("Par", ("participating", "with profit", "with-profit")),
]


def _product_category(text: str) -> Optional[str]:
    t = (text or "").lower()
    for cat, needles in _PRODUCT_HINTS:
        if any(n in t for n in needles):
            return cat
    return None


def _index_threads(ledger_rows: List[Dict]) -> Dict[str, List[Dict]]:
    """parent comment_id -> list of {role, comment} replies (per source_file)."""
    by_parent: Dict[str, List[Dict]] = defaultdict(list)
    for r in ledger_rows:
        pid = r.get("parent_comment_id")
        if pid is not None:
            key = f"{r.get('source_file')}|{pid}"
            by_parent[key].append({"role": r.get("role"), "comment": r.get("comment")})
    return by_parent


class PrecedentIngestionService:
    async def build_rows(
        self, ledger_rows: List[Dict], remediation_rows: List[Dict], cache_dir: str
    ) -> List[Dict[str, Any]]:
        rem_by_ticket: Dict[str, List[Dict]] = defaultdict(list)
        for r in remediation_rows:
            rem_by_ticket[str(r.get("ticket"))].append(r)
        threads_by_parent = _index_threads(ledger_rows)

        # canonical_hash -> aggregated row
        agg: Dict[str, Dict[str, Any]] = {}
        filtered_records = []
        filtered_indices = []
        for idx, r in enumerate(ledger_rows):
            if r.get("is_reply"):
                continue
            comment = r.get("comment") or ""
            span = r.get("highlighted_text") or ""
            if is_thin_comment(comment) or not span.strip():
                continue
            if not r.get("is_reviewer", False):
                continue

            seed = (r.get("regulation_tags") or ["other"])[0]
            record = {
                "issue_seed_tags": r.get("regulation_tags") or [],
                "highlighted_span": span,
                "span_context": r.get("span_context") or "",
                "reviewer_comment": comment,
                "reviewer_role": r.get("role"),
                "_cache_key": canonical_hash(seed, span, comment),
            }
            filtered_records.append(record)
            filtered_indices.append(idx)

        # Batch enrich concurrently using Semaphore
        sem = asyncio.Semaphore(20)

        async def enrich_with_sem(rec):
            async with sem:
                try:
                    return await enrich(rec, cache_dir=cache_dir)
                except Exception as e:
                    logger.warning("Failed to enrich record %s: %s", rec.get("_cache_key"), e)
                    from app.services.precedent.enrichment import PrecedentEnrichment
                    return PrecedentEnrichment(
                        issue_type="General Compliance",
                        why_rationale=rec.get("reviewer_comment") or "Compliance reviewer comment needing alignment",
                        guideline_ref=None,
                        severity="moderate"
                    )

        enrichments = await asyncio.gather(*(enrich_with_sem(rec) for rec in filtered_records))

        for r_orig_idx, r_rec, enriched in zip(filtered_indices, filtered_records, enrichments):
            r = ledger_rows[r_orig_idx]
            span = r_rec["highlighted_span"]
            comment = r_rec["reviewer_comment"]

            chash = canonical_hash(enriched.issue_type, span, comment)
            before, after = link_remediation(span, rem_by_ticket.get(str(r.get("ticket")), []))
            thread = threads_by_parent.get(f"{r.get('source_file')}|{r.get('comment_id')}", [])

            existing = agg.get(chash)
            if existing:
                existing["occurrence_count"] += 1
                if str(r.get("ticket")) not in existing["example_tickets"]:
                    existing["example_tickets"].append(str(r.get("ticket")))
                # keep the richest fix / context we have
                if not existing.get("after_text") and after:
                    existing["before_text"], existing["after_text"] = before, after
                existing["thread"].extend(thread)
                continue

            agg[chash] = {
                "id": precedent_id(chash),
                "canonical_hash": chash,
                "highlighted_span": span,
                "span_context": r_rec["span_context"],
                "reviewer_comment": comment,
                "reviewer_role": r_rec["reviewer_role"],
                "is_reviewer": bool(r.get("is_reviewer", False)),
                "thread": list(thread),
                "resolved": bool(r.get("resolved", False)),
                "before_text": before,
                "after_text": after,
                "regulation_tags": r.get("regulation_tags") or [],
                "issue_type": enriched.issue_type,
                "why_rationale": enriched.why_rationale,
                "guideline_ref": enriched.guideline_ref,
                "severity": enriched.severity,
                "product_category": _product_category(
                    (r.get("title") or "") + " " + span + " " + (r.get("span_context") or "")
                ),
                "ticket": str(r.get("ticket")),
                "source_file": r.get("source_file"),
                "comment_date": r.get("date") or None,
                "occurrence_count": 1,
                "example_tickets": [str(r.get("ticket"))],
            }
        return list(agg.values())

    async def ingest(
        self, ledger_path: str, remediation_path: str, cache_dir: str,
        batch_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        from app.services.rag.indexers.precedent_indexer import upsert_precedents

        started = datetime.utcnow()
        ledger_rows = _load_jsonl(ledger_path)
        rem_rows = _load_jsonl(remediation_path) if os.path.exists(remediation_path) else []
        rows = await self.build_rows(ledger_rows, rem_rows, cache_dir)

        bs = batch_size or settings.kb_batch_size
        inserted = 0
        for i in range(0, len(rows), bs):
            inserted += await upsert_precedents(rows[i : i + bs])
        return {
            "ledger_rows": len(ledger_rows),
            "canonical_precedents": len(rows),
            "indexed": inserted,
            "elapsed_seconds": round((datetime.utcnow() - started).total_seconds(), 2),
        }


def _load_jsonl(path: str) -> List[Dict]:
    out: List[Dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


_singleton: Optional[PrecedentIngestionService] = None


def get_precedent_ingestion_service() -> PrecedentIngestionService:
    global _singleton
    if _singleton is None:
        _singleton = PrecedentIngestionService()
    return _singleton
