"""Precedent indexer — embeds the issue-centric context signature and
batch-upserts rows into precedent_cases. Mirrors compliance_examples_indexer.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List

from app.services.rag.errors import RAGDegraded, RAGIndexingFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.rag.ports import VectorDoc

logger = logging.getLogger(__name__)

# Keys copied verbatim from row -> VectorDoc.fields (everything the store maps).
_FIELD_KEYS = (
 "canonical_hash", "highlighted_span", "span_context", "reviewer_comment",
 "reviewer_role", "is_reviewer", "thread", "resolved", "before_text",
 "after_text", "regulation_tags", "issue_type", "why_rationale",
 "guideline_ref", "severity", "product_category", "ticket", "source_file",
 "comment_date", "occurrence_count", "example_tickets",
)


def build_signature_text(
 highlighted_span: str, span_context: str, issue_type: str, why_rationale: str
) -> str:
 """The context signature: encodes the *kind of problem*, not one phrasing.
 Embedded so paraphrased violations still match."""
 return (
 f"Issue: {issue_type}\n"
 f"Why: {why_rationale}\n"
 f"Flagged text: {highlighted_span}\n"
 f"Context: {span_context}"
 )


def _row_to_doc(row: Dict[str, Any], embedding: List[float]) -> VectorDoc:
 fields = {k: row.get(k) for k in _FIELD_KEYS}
 fields["embed_text"] = build_signature_text(
 row.get("highlighted_span", ""), row.get("span_context", "") or "",
 row.get("issue_type", "") or "", row.get("why_rationale", "") or "",
 )
 return VectorDoc(id=row["id"], embedding=embedding, fields=fields)


async def upsert_precedents(rows: Iterable[Dict[str, Any]]) -> int:
 rows = list(rows)
 if not rows:
 return 0
 # canonical_hash is the store's hard-required dedup key (NOT NULL UNIQUE);
 # fail fast rather than bind NULL into the upsert.
 missing = [r.get("id") for r in rows if not r.get("canonical_hash")]
 if missing:
 raise RAGIndexingFailed(
 f"{len(missing)} precedent row(s) missing canonical_hash: {missing[:5]}"
 )
 embedder = get_embedder()
 store = get_vector_store()
 try:
 texts = [
 build_signature_text(
 r.get("highlighted_span", ""), r.get("span_context", "") or "",
 r.get("issue_type", "") or "", r.get("why_rationale", "") or "",
 )
 for r in rows
 ]
 vectors = await embedder.embed(texts)
 if len(vectors) != len(rows):
 raise RAGIndexingFailed(
 f"embedder returned {len(vectors)} vectors for {len(rows)} rows"
 )
 docs = [_row_to_doc(r, v) for r, v in zip(rows, vectors)]
 await store.upsert("precedent_cases", docs)
 logger.info(f"Indexed {len(docs)} precedents into precedent_cases")
 return len(docs)
 except RAGDegraded as e:
 raise RAGIndexingFailed(str(e)) from e
