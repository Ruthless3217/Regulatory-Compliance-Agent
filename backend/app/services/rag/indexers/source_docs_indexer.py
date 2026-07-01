"""Source-docs indexer — chunks a regulator PDF, embeds passages, stores
them in rag_source_docs, and supports backfilling `derived_rule_ids` after
the LLM extracts rules from those passages.

Call sequence used by rule_generator_service:

 doc_id = uuid.uuid4()
 passages = await index_source_document(
 document_id=doc_id, document_title=title, regulator='irdai',
 full_text=parsed_pdf_text,
 ) # returns [(passage_id, text), ...]

 # ... LLM extracts rules from one or more passages ...

 await link_rules_to_passage(
 passage_id=passages[k][0], rule_ids=[r1.id, r2.id]
 )
"""
from __future__ import annotations

import logging
import uuid
from typing import List, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.rag.errors import RAGDegraded, RAGIndexingFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.rag.ports import VectorDoc

logger = logging.getLogger(__name__)


_PASSAGE_TOKEN_CAP = 1200 # passages are coarser than analysis chunks


def _chunk_source_text(content: str) -> List[str]:
 """Reuse the project's token chunker if available; fall back to paragraphs."""
 try:
 import tiktoken
 enc = tiktoken.get_encoding("cl100k_base")
 tokens = enc.encode(content)
 passages: List[str] = []
 start = 0
 while start < len(tokens):
 end = min(start + _PASSAGE_TOKEN_CAP, len(tokens))
 passages.append(enc.decode(tokens[start:end]))
 start = end
 return passages or [content]
 except ImportError:
 paras = [p.strip() for p in content.split("\n\n") if p.strip()]
 return paras or [content]


async def index_source_document(
 document_id: uuid.UUID | str,
 document_title: str,
 regulator: str,
 full_text: str,
) -> List[Tuple[str, str]]:
 """Chunk + embed + upsert. Returns [(passage_id, passage_text), ...] in order."""
 if not full_text:
 return []
 passages = _chunk_source_text(full_text)
 embedder = get_embedder()
 store = get_vector_store()
 try:
 vectors = await embedder.embed(passages)
 docs: List[VectorDoc] = []
 result: List[Tuple[str, str]] = []
 for i, (passage, vec) in enumerate(zip(passages, vectors)):
 pid = str(uuid.uuid4())
 docs.append(
 VectorDoc(
 id=pid,
 embedding=vec,
 fields={
 "document_id": str(document_id),
 "document_title": document_title,
 "regulator": regulator,
 "chunk_index": i,
 "page_number": None,
 "text": passage,
 "derived_rule_ids": [],
 },
 )
 )
 result.append((pid, passage))
 await store.upsert("rag_source_docs", docs)
 logger.info(
 f"Indexed source doc '{document_title}' ({regulator}): {len(docs)} passages"
 )
 return result
 except RAGDegraded as e:
 raise RAGIndexingFailed(str(e)) from e


async def link_rules_to_passage(
 passage_id: uuid.UUID | str, rule_ids: List[uuid.UUID | str]
) -> None:
 """Backfill `derived_rule_ids` on a stored passage.

 pgvector path uses a raw UPDATE — no need to re-embed. Azure Search
 path goes through the store's upsert (merge_or_upload). We do the
 cheap pgvector path inline; for Azure we'd need a separate read-then-upsert
 (out of scope for v1 — Azure backend is enabled later).
 """
 if not rule_ids:
 return
 # pgvector fast path
 db: Session = SessionLocal()
 try:
 from app.services.rag.stores.pgvector_store import _uuid_array_literal
 arr_lit = _uuid_array_literal(rule_ids)
 db.execute(
 text(
 """
 UPDATE rag_source_docs
 SET derived_rule_ids = (
 SELECT ARRAY(SELECT DISTINCT unnest(derived_rule_ids || CAST(:arr AS UUID[])))
 )
 WHERE id = CAST(:pid AS UUID)
 """
 ),
 {"arr": arr_lit, "pid": str(passage_id)},
 )
 db.commit()
 except Exception as e:
 db.rollback()
 logger.error(f"link_rules_to_passage failed: {e}")
 raise RAGIndexingFailed(str(e)) from e
 finally:
 db.close()
