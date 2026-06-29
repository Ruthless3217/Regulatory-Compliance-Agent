"""Deterministic dedup key for precedent records.

Two precedents that share an issue_type, a normalized highlighted span and a
normalized reviewer comment ARE the same precedent — they collapse to one row
with an occurrence_count. The hash is the UNIQUE key; precedent_id derives a
stable UUID PK from it so re-ingest is idempotent.
"""
from __future__ import annotations

import hashlib
import re
import uuid

_NS = uuid.UUID("6f1c0b2e-0000-4000-8000-00000000000c")  # precedent_cases namespace
_WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").strip().lower())


def canonical_hash(issue_type: str, highlighted_span: str, reviewer_comment: str) -> str:
    key = "\x1f".join((_norm(issue_type), _norm(highlighted_span), _norm(reviewer_comment)))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def precedent_id(canonical_hash: str) -> str:
    return str(uuid.uuid5(_NS, canonical_hash))
