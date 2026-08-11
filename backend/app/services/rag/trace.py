"""Per-run retrieval trace — the per-leg evidence that used to die inside
``PgVectorStore.hybrid_search._do()``.

The store computes a cosine leg, a ts_rank leg and an RRF fusion, then throws
all three away and returns only the fused survivors. So "why is this rule not
in the prompt?" had no answer: the fused score reached the applicability judge,
the leg scores and ranks did not, and everything that lost the fusion was
invisible.

Collection is a **contextvar**, not a parameter: ``hybrid_search`` is part of
the ``VectorStore`` Protocol (rag/ports.py) implemented by
``azure_search_store`` too, and a new kwarg would break both. A contextvar also
costs one ``.get()`` when nothing is collecting — dispatch_node is the only
caller that starts a trace, so every other retrieval path stays untouched.

Two vars, because one query's rows are meaningless without knowing which chunk
asked:

* ``_trace``  — the collector, set once by dispatch_node around retrieval.
* ``_query``  — {chunk_id, category}, set by the retriever inside each
  per-(chunk, category) coroutine. The SAME rule retrieved for two chunks has
  two different cosines; without this the rows could not be attributed.

Thread note: ``hybrid_search`` reads both vars in its async body (where the
context is intact) and hands a ``QueryTracer`` into the executor thread —
``loop.run_in_executor`` does NOT copy the context, so a ``.get()`` from inside
``_do()`` would always see the default.

Persistence (``persist_run_candidates``) joins the trace against the
applicability verdicts and writes one row per candidate into
``retrieval_candidates`` (migration 0037). It owns the final_status /
deciding_stage vocabulary — see ``_FINAL_STATUS_DOC``.
"""
from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

# Candidates ranked below the fused cut are still recorded, but only for the
# near-miss band: "it was retrieved and came 11th, the cut was 8" is the answer
# a curator needs; rank 400 of a 60-row recall pool is noise, and a real run
# fires ~700 queries. The band is per query, so the bound is explainable
# (top_k + 10 rows/query) rather than a global truncation of an unknown
# population.
NEAR_MISS = 10

# applicability.py speaks "rules"/"precedents"; the store speaks table names.
# Rows carry the applicability vocabulary (it is what the inspector filters on)
# and keep the real table in `filters.index`.
_CORPUS_BY_INDEX = {
    "rag_rules": "rules",
    "precedent_cases": "precedents",
    "rag_compliance_examples": "precedents",
}

_FINAL_STATUS_DOC = """
final_status              deciding_stage        meaning
------------------------  --------------------  --------------------------------
in_prompt                 prompt                Reached the analysis prompt for
                                                its chunk. Nothing dropped it.
dropped_by_cap            rule_cap              Accepted by applicability, then
                                                cut by _MAX_RULES_PER_CHUNK.
unused_fallback           fallback_unused       Accepted in the flat active-rule
                                                set, which a non-degraded run
                                                never feeds to a prompt.
rejected_applicability    applicability         Refused by the applicability
                                                judge (reason is verbatim).
below_threshold           score_threshold       Survived fusion, then fell under
                                                settings.rag_score_threshold in
                                                the rules retriever.
dropped_by_retriever      retriever_filter      Survived fusion, then dropped by
                                                a corpus-specific post-filter
                                                (thin precedent comment, or the
                                                same-submission leakage guard).
not_retrieved_far_enough  fusion               Retrieved by a leg but ranked
                                                outside the fused top_k, so the
                                                applicability gate never saw it.
"""


@dataclass(frozen=True)
class TracedCandidate:
    """One candidate as the store saw it, for one query."""
    corpus: str
    index: str
    chunk_id: Optional[str]
    category: Optional[str]
    candidate_id: str
    retrieval_method: str          # "vector" | "bm25" | "both"
    cosine: Optional[float]
    vector_rank: Optional[int]     # 1-based within the cosine leg
    ts_rank: Optional[float]
    bm25_rank: Optional[int]       # 1-based within the ts_rank leg
    fused_score: float
    fused_rank: int                # 1-based within the fused list
    top_k: int
    filters: Optional[Dict[str, Any]]

    @property
    def key(self) -> Tuple[str, Optional[str], Optional[str], str]:
        return (self.corpus, self.chunk_id, self.category, self.candidate_id)


class RetrievalTrace:
    """Collector for one analysis run. Mutated from executor threads."""

    def __init__(self) -> None:
        self.rows: List[TracedCandidate] = []
        self._lock = threading.Lock()

    def extend(self, rows: List[TracedCandidate]) -> None:
        with self._lock:
            self.rows.extend(rows)


class QueryTracer:
    """One hybrid_search call. Built in the async body, used in the thread."""

    def __init__(self, trace: RetrievalTrace, *, index: str, top_k: int,
                 filters: Optional[Dict[str, Any]], chunk_id: Optional[str],
                 category: Optional[str]) -> None:
        self._trace = trace
        self.index = index
        self.top_k = top_k
        self.filters = {"index": index, "applied": filters or None}
        self.chunk_id = chunk_id
        self.category = category

    def record(
        self,
        vec_ranked: Sequence[Tuple[str, float]],
        kw_ranked: Sequence[Tuple[str, float]],
        fused: Sequence[Tuple[str, float]],
    ) -> None:
        """`fused` is the FULL fused list, before the top_k slice."""
        try:
            vec = {doc_id: (i + 1, score) for i, (doc_id, score) in enumerate(vec_ranked)}
            kw = {doc_id: (i + 1, score) for i, (doc_id, score) in enumerate(kw_ranked)}
            corpus = _CORPUS_BY_INDEX.get(self.index, self.index)
            keep = self.top_k + NEAR_MISS
            rows: List[TracedCandidate] = []
            for i, (doc_id, fused_score) in enumerate(fused[:keep]):
                v = vec.get(doc_id)
                k = kw.get(doc_id)
                rows.append(TracedCandidate(
                    corpus=corpus,
                    index=self.index,
                    chunk_id=self.chunk_id,
                    category=self.category,
                    candidate_id=str(doc_id),
                    # Set membership of the two leg lists IS the method: a doc
                    # in both was found semantically AND lexically.
                    retrieval_method=("both" if v and k else "vector" if v else "bm25"),
                    cosine=(v[1] if v else None),
                    vector_rank=(v[0] if v else None),
                    ts_rank=(k[1] if k else None),
                    bm25_rank=(k[0] if k else None),
                    fused_score=float(fused_score),
                    fused_rank=i + 1,
                    top_k=self.top_k,
                    filters=self.filters,
                ))
            self._trace.extend(rows)
        except Exception as e:  # noqa: BLE001 - tracing must never break retrieval
            logger.debug("retrieval trace record failed (non-fatal): %s", e)


# ------------------------------------------------------------------ context --

_trace: ContextVar[Optional[RetrievalTrace]] = ContextVar("retrieval_trace", default=None)
_query: ContextVar[Optional[Dict[str, Any]]] = ContextVar("retrieval_query", default=None)


def start() -> Any:
    """Begin collecting. Returns a token for :func:`stop`."""
    return _trace.set(RetrievalTrace())


def stop(token) -> List[TracedCandidate]:
    """Stop collecting and return everything recorded."""
    trace = _trace.get()
    try:
        _trace.reset(token)
    except (ValueError, LookupError):  # token from another context
        _trace.set(None)
    return list(trace.rows) if trace is not None else []


@contextmanager
def query(*, chunk_id: Optional[str] = None, category: Optional[str] = None):
    """Label every hybrid_search inside this block with its asking chunk."""
    token = _query.set({"chunk_id": chunk_id, "category": category})
    try:
        yield
    finally:
        _query.reset(token)


def begin_query(*, index: str, top_k: int, filters: Optional[Dict[str, Any]]) -> Optional[QueryTracer]:
    """Called by the store in its ASYNC body. None (the common case) means no
    collector is active and the store does no extra work at all."""
    trace = _trace.get()
    if trace is None:
        return None
    ctx = _query.get() or {}
    return QueryTracer(
        trace, index=index, top_k=top_k, filters=filters,
        chunk_id=ctx.get("chunk_id"), category=ctx.get("category"),
    )


# -------------------------------------------------------------- persistence --

_INSERT = """
INSERT INTO retrieval_candidates (
    run_id, chunk_id, corpus, tier, category, candidate_id, retrieval_method,
    cosine, ts_rank, vector_rank, bm25_rank, fused_score, fused_rank, filters,
    scope_value, verdict, reason, final_status, deciding_stage
) VALUES (
    CAST(:run_id AS UUID), :chunk_id, :corpus, :tier, :category, :candidate_id,
    :retrieval_method, :cosine, :ts_rank, :vector_rank, :bm25_rank,
    :fused_score, :fused_rank, CAST(:filters AS JSONB),
    :scope_value, :verdict, :reason, :final_status, :deciding_stage
)
"""


def _gated_status(record: Dict[str, Any], in_prompt: bool) -> Tuple[str, str]:
    if record.get("verdict") == "rejected":
        return "rejected_applicability", "applicability"
    if in_prompt:
        return "in_prompt", "prompt"
    if record.get("tier") == "active_rules_fallback":
        return "unused_fallback", "fallback_unused"
    return "dropped_by_cap", "rule_cap"


def _ungated_status(cand: TracedCandidate, score_threshold: float) -> Tuple[str, str]:
    """A traced candidate the applicability judge never received."""
    if cand.fused_rank > cand.top_k:
        return "not_retrieved_far_enough", "fusion"
    if cand.corpus == "rules" and cand.fused_score < score_threshold:
        return "below_threshold", "score_threshold"
    # Precedents: rejected by _is_thin (pure-response comment) or by the
    # same-submission leakage guard, both in precedent_retriever.
    return "dropped_by_retriever", "retriever_filter"


def build_rows(
    run_id: str,
    *,
    traced: Sequence[TracedCandidate],
    gated: Sequence[Dict[str, Any]],
    prompt_keys: Set[Tuple[str, Optional[str], str]],
    score_threshold: float,
) -> List[Dict[str, Any]]:
    """One row per candidate: every applicability verdict, plus every traced
    candidate that never reached the judge.

    ``gated`` records are the applicability debug dicts stamped by dispatch_node
    (corpus, id, score, scope_value, verdict, reason, tier, chunk_id, category).
    ``prompt_keys`` are the (corpus, chunk_id, candidate_id) triples that
    actually reached an analysis prompt.
    """
    by_key: Dict[Tuple[str, Optional[str], Optional[str], str], TracedCandidate] = {}
    for cand in traced:
        by_key.setdefault(cand.key, cand)

    rows: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, Optional[str], Optional[str], str]] = set()

    for rec in gated:
        corpus = rec.get("corpus") or "unknown"
        chunk_id = rec.get("chunk_id")
        category = rec.get("category")
        cid = str(rec.get("id"))
        key = (corpus, chunk_id, category, cid)
        seen.add(key)
        cand = by_key.get(key)
        in_prompt = (corpus, chunk_id, cid) in prompt_keys
        status, stage = _gated_status(rec, in_prompt)
        rows.append({
            "run_id": run_id,
            "chunk_id": chunk_id,
            "corpus": corpus,
            "tier": rec.get("tier"),
            "category": category,
            "candidate_id": cid,
            "retrieval_method": cand.retrieval_method if cand else None,
            "cosine": cand.cosine if cand else None,
            "ts_rank": cand.ts_rank if cand else None,
            "vector_rank": cand.vector_rank if cand else None,
            "bm25_rank": cand.bm25_rank if cand else None,
            # The fused score is what applicability recorded as `score`; the
            # trace is preferred because it is the unrounded source.
            "fused_score": cand.fused_score if cand else _as_float(rec.get("score")),
            "fused_rank": cand.fused_rank if cand else None,
            "filters": json.dumps(cand.filters) if cand else None,
            "scope_value": _as_text(rec.get("scope_value")),
            "verdict": rec.get("verdict"),
            "reason": rec.get("reason"),
            "final_status": status,
            "deciding_stage": stage,
        })

    for cand in traced:
        if cand.key in seen:
            continue
        seen.add(cand.key)
        status, stage = _ungated_status(cand, score_threshold)
        rows.append({
            "run_id": run_id,
            "chunk_id": cand.chunk_id,
            "corpus": cand.corpus,
            "tier": None,
            "category": cand.category,
            "candidate_id": cand.candidate_id,
            "retrieval_method": cand.retrieval_method,
            "cosine": cand.cosine,
            "ts_rank": cand.ts_rank,
            "vector_rank": cand.vector_rank,
            "bm25_rank": cand.bm25_rank,
            "fused_score": cand.fused_score,
            "fused_rank": cand.fused_rank,
            "filters": json.dumps(cand.filters),
            "scope_value": None,
            # NULL verdict is the honest value: the judge never saw this row.
            "verdict": None,
            "reason": None,
            "final_status": status,
            "deciding_stage": stage,
        })
    return rows


def _as_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _as_text(v) -> Optional[str]:
    return None if v is None else str(v)


def persist_run_candidates(db, run_id: Optional[str], rows: List[Dict[str, Any]]) -> int:
    """Bulk-insert the run's candidate rows. Returns the count written.

    Fail-soft by construction, and specifically SAVEPOINT-scoped: on Postgres a
    failed statement aborts the whole transaction, so a missing table (migration
    0037 not applied) would otherwise poison the graph's session and take the
    analysis down with it. The nested transaction rolls back to the savepoint
    and leaves the outer transaction usable.

    No commit here — the rows ride the graph session's transaction, which
    run_tracker.close_run commits alongside the run itself.
    """
    if not run_id or not rows:
        return 0
    try:
        from sqlalchemy import text as sa_text

        with db.begin_nested():
            db.execute(sa_text(_INSERT), rows)
        return len(rows)
    except Exception as e:  # noqa: BLE001 - observability must not break analysis
        logger.warning("retrieval trace persist failed (non-fatal): %s", e)
        return 0
