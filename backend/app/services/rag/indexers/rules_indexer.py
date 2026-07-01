"""Rules indexer — keeps `rag_rules` in sync with the `rules` Postgres table.

`embed_text` for a rule = `[{category}] [{severity}] {rule_text}. Keywords: ...`
Prepending category + severity as natural-language tokens lets retrieval
respect them without explicit filters.
"""
from __future__ import annotations

import logging
import uuid
from typing import Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.rule import Rule
from app.services.rag.errors import RAGDegraded, RAGIndexingFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.rag.ports import VectorDoc

logger = logging.getLogger(__name__)


def _embed_text_for_rule(rule: Rule) -> str:
 keywords = rule.keywords or []
 if isinstance(keywords, str):
 keywords = [keywords]
 kw = ", ".join(str(k) for k in keywords) if keywords else "(none)"
 return f"[{rule.category}] [{rule.severity}] {rule.rule_text}. Keywords: {kw}"


def _rule_to_doc(rule: Rule, embedding: List[float]) -> VectorDoc:
 return VectorDoc(
 id=str(rule.id),
 embedding=embedding,
 fields={
 "category": rule.category,
 "severity": rule.severity,
 "is_active": bool(rule.is_active),
 "rule_text": rule.rule_text,
 "keywords": rule.keywords or [],
 "embed_text": _embed_text_for_rule(rule),
 "source": rule.generation_source,
 },
 )


async def upsert_rule(rule_id: uuid.UUID | str, db: Session) -> None:
 """Embed and upsert a single rule. Safe to call after `db.commit()`."""
 rule = db.query(Rule).filter(Rule.id == str(rule_id)).first()
 if not rule:
 logger.warning(f"upsert_rule: rule {rule_id} not found")
 return
 await upsert_rules([rule])


async def upsert_rules(rules: Iterable[Rule]) -> None:
 rules = list(rules)
 if not rules:
 return
 embedder = get_embedder()
 store = get_vector_store()
 try:
 texts = [_embed_text_for_rule(r) for r in rules]
 vectors = await embedder.embed(texts)
 docs = [_rule_to_doc(r, v) for r, v in zip(rules, vectors)]
 await store.upsert("rag_rules", docs)
 logger.info(f"Indexed {len(docs)} rules into rag_rules")
 except RAGDegraded as e:
 # Degraded means the backend is unreachable. Surface as indexing failure
 # so the caller can decide whether to retry; do NOT roll back the DB.
 raise RAGIndexingFailed(str(e)) from e


async def upsert_all_active_rules(db: Session) -> int:
 """Backfill: re-index every active rule. Idempotent."""
 rules = db.query(Rule).filter(Rule.is_active == True).all() # noqa: E712
 await upsert_rules(rules)
 return len(rules)


async def delete_rule(rule_id: uuid.UUID | str) -> None:
 store = get_vector_store()
 try:
 await store.delete("rag_rules", [str(rule_id)])
 except RAGDegraded as e:
 raise RAGIndexingFailed(str(e)) from e
