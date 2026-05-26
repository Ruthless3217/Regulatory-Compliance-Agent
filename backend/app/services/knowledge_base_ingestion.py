"""Knowledge-base ingestion — parse _rl JSON reviewer decisions into precedent
rows and index them into rag_compliance_examples.

Corpus schema (dataset_2.1_rl/*.json):
    task, instruction,
    input.draft_text, input.compliance_comments (str),
    output.final_text,
    metadata.document_id, metadata.title

compliance_comments lines look like:
    [Reviewer Name/Org]: the comment text (Context: anchor text…)
Anchors are truncated (~80 chars + ellipsis); alignment uses fuzzy prefix match.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

# Deterministic namespace so re-ingesting the same (source_file, chunk, comment)
# produces the same id → ON CONFLICT (id) makes ingestion idempotent.
_NS = uuid.UUID("6f1c0b2e-0000-4000-8000-000000000004")

_COMMENT_RE = re.compile(
    r"^\[(?P<rev>.+?)\]:\s*(?P<comment>.*?)\s*\(Context:\s*(?P<anchor>.*?)\)\s*$"
)

_CATEGORY_KEYWORDS = [
    ("terminology issue", ("terminology", "rephrase", "word", "rename", "phrase")),
    ("missing reference", ("link", "url", "reference", "refer", "source", "cite")),
    ("disclaimer issue", ("disclaimer", "disclosure", "disclaim")),
    ("legal language", ("legal", "legally", "irdai", "regulatory", "regulation", "compliance")),
]

PARSE_ERROR_LOG = os.path.join("logs", "parse_errors.log")


@dataclass
class ParsedComment:
    reviewer: str
    comment: str
    anchor: str


def _strip_ellipsis(s: str) -> str:
    s = s.strip()
    for suffix in ("…", "..."):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s


def _log_parse_error(source_file: str, line_no: int, line: str) -> None:
    os.makedirs("logs", exist_ok=True)
    with open(PARSE_ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(f"{source_file}\tline {line_no}\t{line}\n")


def parse_compliance_comments(raw: Optional[str], source_file: str) -> List[ParsedComment]:
    """Parse the compliance_comments string into structured comments.
    Malformed lines are logged (never silently dropped)."""
    if not raw or not raw.strip():
        return []
    out: List[ParsedComment] = []
    for i, line in enumerate(raw.split("\n"), start=1):
        line = line.strip()
        if not line:
            continue
        m = _COMMENT_RE.match(line)
        if not m:
            _log_parse_error(source_file, i, line)
            continue
        out.append(
            ParsedComment(
                reviewer=m.group("rev").strip(),
                comment=m.group("comment").strip(),
                anchor=_strip_ellipsis(m.group("anchor")),
            )
        )
    return out


def classify_category(comment: str) -> str:
    c = (comment or "").lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(k in c for k in keywords):
            return category
    return "other"


def classify_severity(reviewer_name: str) -> str:
    r = reviewer_name or ""
    if re.search(r"\bLegal\b|\bCompliance\b", r, re.IGNORECASE):
        return "critical"
    if re.search(r"\bMarketing\b", r, re.IGNORECASE):
        return "moderate"
    return "informational"


def align_comment_to_chunk(
    anchor: str, chunks: List[str], min_fuzzy: int
) -> Tuple[Optional[int], int]:
    """Return (chunk_index, score). Exact substring → 100; else best fuzzy
    partial_ratio on first 100 chars if >= min_fuzzy; else (None, best)."""
    if not anchor or not chunks:
        return None, 0
    needle = anchor[:100]
    for idx, ch in enumerate(chunks):
        if anchor and anchor in ch:
            return idx, 100
    best_idx, best_score = None, 0
    for idx, ch in enumerate(chunks):
        score = int(fuzz.partial_ratio(needle, ch[:400]))
        if score > best_score:
            best_idx, best_score = idx, score
    if best_idx is not None and best_score >= min_fuzzy:
        return best_idx, best_score
    return None, best_score


def _chunk_text(content: str) -> List[str]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.kb_chunk_size,
        chunk_overlap=settings.kb_chunk_overlap,
    )
    return [c for c in splitter.split_text(content or "") if c.strip()]


def _pair_final_chunk(final_chunks: List[str], draft_idx: int, n_draft_chunks: int) -> Optional[str]:
    """Positional pairing of a draft chunk to a final chunk.

    Returns a final chunk only when the draft and final chunk counts match,
    ensuring positional alignment is reliable.  When counts differ (reviewer
    merged or split paragraphs) we return None rather than attach a wrong
    approved rewrite to the precedent.
    """
    if not final_chunks or len(final_chunks) != n_draft_chunks:
        return None
    return final_chunks[draft_idx] if draft_idx < len(final_chunks) else None


class KnowledgeBaseIngestionService:
    """Parses _rl JSON files into precedent rows and indexes them."""

    def parse_file(self, path: str) -> Dict[str, Any]:
        """Parse one file into {rows, unmatched, document_id}. Raises on missing fields."""
        source_file = os.path.basename(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        inp = data.get("input") or {}
        out = data.get("output") or {}
        meta = data.get("metadata") or {}
        draft = inp.get("draft_text")
        if not isinstance(draft, str) or not draft.strip():
            raise ValueError(f"{source_file}: missing input.draft_text")

        document_id = str(meta.get("document_id") or "")
        title = meta.get("title")
        task = data.get("task")
        final_text = out.get("final_text") or ""

        draft_chunks = _chunk_text(draft)
        final_chunks = _chunk_text(final_text)
        comments = parse_compliance_comments(inp.get("compliance_comments"), source_file)

        rows: List[Dict[str, Any]] = []
        unmatched = 0
        for pc in comments:
            idx, _score = align_comment_to_chunk(
                pc.anchor, draft_chunks, settings.kb_min_fuzzy_score
            )
            if idx is None:
                unmatched += 1
                logger.debug(f"{source_file}: unmatched comment anchor: {pc.anchor[:60]!r}")
                continue
            chunk_text = draft_chunks[idx]
            comment_text = pc.comment
            key = f"{source_file}|{chunk_text}|{comment_text}|{pc.anchor}|{pc.reviewer}"
            rows.append(
                {
                    "id": str(uuid.uuid5(_NS, key)),
                    "document_id": document_id,
                    "title": title,
                    "task": task,
                    "section_label": None,
                    "chunk_text": chunk_text,
                    "anchor_text": pc.anchor,
                    "reviewer_name": pc.reviewer,
                    "comment_text": comment_text,
                    "final_text_chunk": _pair_final_chunk(final_chunks, idx, len(draft_chunks)),
                    "violation_category": classify_category(comment_text),
                    "severity": classify_severity(pc.reviewer),
                    "source_file": source_file,
                }
            )
        return {"rows": rows, "unmatched": unmatched, "document_id": document_id}

    def _existing_ids(self, ids: List[str]) -> set:
        if not ids:
            return set()
        db = SessionLocal()
        try:
            res = db.execute(
                text("SELECT id FROM rag_compliance_examples WHERE id = ANY(CAST(:ids AS UUID[]))"),
                {"ids": "{" + ",".join(ids) + "}"},
            ).all()
            return {str(r[0]) for r in res}
        finally:
            db.close()

    async def _existing_ids_async(self, ids: List[str]) -> set:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._existing_ids, ids)

    async def ingest_folder(
        self, folder: str, limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """Parse + index every *.json in folder. Returns a summary dict."""
        from app.services.rag.indexers.compliance_examples_indexer import upsert_examples

        files = sorted(
            os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".json")
        )
        if limit:
            files = files[:limit]

        started = datetime.utcnow()
        processed = failed = inserted = skipped = unmatched_total = 0
        doc_ids: Dict[str, int] = {}
        batch: List[Dict[str, Any]] = []

        async def flush(rows: List[Dict[str, Any]]) -> Tuple[int, int]:
            if not rows:
                return 0, 0
            existing = await self._existing_ids_async([r["id"] for r in rows])
            fresh = [r for r in rows if r["id"] not in existing]
            n = await upsert_examples(fresh)
            return n, len(rows) - len(fresh)

        for path in files:
            try:
                parsed = self.parse_file(path)
            except Exception as e:
                failed += 1
                logger.warning(f"Ingest parse failed for {os.path.basename(path)}: {e}")
                continue
            processed += 1
            unmatched_total += parsed["unmatched"]
            did = parsed["document_id"]
            doc_ids[did] = doc_ids.get(did, 0) + 1
            batch.extend(parsed["rows"])
            if len(batch) >= settings.kb_batch_size:
                n, dup = await flush(batch)
                inserted += n
                skipped += dup
                batch = []

        n, dup = await flush(batch)
        inserted += n
        skipped += dup

        duplicate_doc_ids = {d: c for d, c in doc_ids.items() if c > 1}
        return {
            "folder": folder,
            "files_processed": processed,
            "files_failed": failed,
            "inserted": inserted,
            "skipped_duplicates": skipped,
            "unmatched_comments": unmatched_total,
            "distinct_document_ids": len(doc_ids),
            "duplicate_document_id_count": len(duplicate_doc_ids),
            "elapsed_seconds": round((datetime.utcnow() - started).total_seconds(), 2),
        }

    def get_stats(self) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            total = db.execute(text("SELECT COUNT(*) FROM rag_compliance_examples")).scalar() or 0

            def _counts(col: str) -> Dict[str, int]:
                rows = db.execute(
                    text(f"SELECT {col}, COUNT(*) FROM rag_compliance_examples GROUP BY {col}")
                ).all()
                return {str(r[0]): int(r[1]) for r in rows}

            distinct_files = db.execute(
                text("SELECT COUNT(DISTINCT source_file) FROM rag_compliance_examples")
            ).scalar() or 0
            latest = db.execute(
                text("SELECT MAX(created_at) FROM rag_compliance_examples")
            ).scalar()
            return {
                "total": int(total),
                "by_violation_category": _counts("violation_category"),
                "by_severity": _counts("severity"),
                "by_reviewer_name": _counts("reviewer_name"),
                "distinct_source_files": int(distinct_files),
                "most_recent_created_at": latest.isoformat() if latest else None,
            }
        finally:
            db.close()


_singleton: Optional[KnowledgeBaseIngestionService] = None


def get_kb_ingestion_service() -> KnowledgeBaseIngestionService:
    global _singleton
    if _singleton is None:
        _singleton = KnowledgeBaseIngestionService()
    return _singleton
