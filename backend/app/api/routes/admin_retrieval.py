"""Retrieval debugger API (RETRIEVAL_RCA.md §4) — the *inspection* half of
corpus curation.

Nothing here captures data: the dispatch node already records every retrieval
candidate (score, scope tag, accept/reject verdict + reason) into
``metadata["retrieval_debug"]``, and ComplianceEngine.run_metadata_from_state
persists that whitelisted extract onto ``analysis_runs.run_metadata`` (JSONB,
migration 0022). These routes read it back and answer the curation question:
*which documents came out of each cluster, which were refused, and why.*

Honesty contract: runs that predate migration 0022, runs that died before
dispatch, and runs that recorded zero candidates all return an explicit
``{"status": "no_retrieval_data", "reason": ...}``. An empty-but-successful
looking payload would silently read as "retrieval was clean", which is exactly
the wrong conclusion to hand a curator.

Truncation is surfaced rather than hidden: the persisted payload keeps only the
first 100 rejections and the first 100 acceptances, so EVERY count derived from
the records is a sample. Only ``candidates_total`` and ``rejected_total`` count
the whole run — and the accepted population is their difference, never
``candidates_total - records_available`` (the records hold both verdicts, so
that subtraction counts unpersisted rejections as acceptances).

Since migration 0037 there is a SECOND, uncapped source: `retrieval_candidates`,
written by dispatch_node via rag/trace.py. It holds one row per candidate per
(chunk, category) query with the per-leg scores and ranks the store used to
discard, and the stage that decided each candidate's fate. `/runs/{id}/chunks`
and `/runs/{id}/candidates` read it; the three sampled-verdict routes are
unchanged, because every run that predates 0037 still only has the sample.

Gated on ``rules:write`` — the corpus-curation scope (admin + super_admin,
never a plain grader).
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.dependencies import require
from app.models.analysis_run import AnalysisRun
from app.models.rule import Rule
from app.api.routes.admin_console import _iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/retrieval", tags=["Admin Retrieval"])

_PREVIEW = 300  # chars of rule_text / highlighted_span kept per candidate


# --- payload extraction -----------------------------------------------------

def extract_debug(run) -> Tuple[Optional[dict], Optional[str]]:
    """(retrieval_debug payload, reason it is absent). Exactly one is non-None.

    The absent-reason is deliberately specific — "which of the several ways
    this run has no data" is the first thing a curator needs to know."""
    md = getattr(run, "run_metadata", None)
    if not isinstance(md, dict) or not md:
        return None, (
            "analysis_runs.run_metadata is NULL/empty — the run predates "
            "migration 0022, or it failed before observability was written."
        )
    dbg = md.get("retrieval_debug")
    if not isinstance(dbg, dict) or not dbg:
        return None, (
            "run_metadata has no retrieval_debug block — the dispatch node "
            "never completed (degraded or failed run)."
        )
    records = _records(dbg)
    if not records and not dbg.get("candidates_total"):
        return None, (
            "retrieval recorded zero candidates — nothing was retrieved from "
            "any corpus for this run (empty index, or no chunks to retrieve for)."
        )
    return dbg, None


def _records(dbg: dict) -> List[dict]:
    """All per-candidate records the run persisted (rejections + the accepted
    sample), as copies — the originals belong to a live ORM JSONB value."""
    out = []
    for key in ("rejected", "accepted_sample"):
        for rec in dbg.get(key) or []:
            if isinstance(rec, dict):
                out.append(dict(rec))
    return out


# --- grouping ---------------------------------------------------------------

def group_by_corpus_tier(records: List[dict]) -> Dict[str, Dict[str, List[dict]]]:
    out: Dict[str, Dict[str, List[dict]]] = {}
    for r in records:
        corpus = r.get("corpus") or "unknown"
        tier = r.get("tier") or "unknown"
        out.setdefault(corpus, {}).setdefault(tier, []).append(r)
    return out


def count_by_corpus(records: List[dict]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for r in records:
        c = out.setdefault(r.get("corpus") or "unknown", {"accepted": 0, "rejected": 0})
        c["rejected" if r.get("verdict") == "rejected" else "accepted"] += 1
    return out


def reason_code(reason: Optional[str]) -> str:
    """'category_conflict: item=ulip scope=[...] (C1)' -> 'category_conflict'."""
    return (reason or "unknown").split(":", 1)[0].strip() or "unknown"


def group_by_reason(records: List[dict]) -> Dict[str, dict]:
    """Rejections bucketed by reason code — "what did we wrongly exclude" is
    the question that drives corpus fixes."""
    out: Dict[str, dict] = {}
    for r in records:
        bucket = out.setdefault(reason_code(r.get("reason")), {"count": 0, "candidates": []})
        bucket["count"] += 1
        bucket["candidates"].append(r)
    return out


# --- enrichment (best-effort; a missing referent degrades to the bare id) ----

def _valid_uuids(ids) -> List[_uuid.UUID]:
    out = []
    for i in ids:
        try:
            out.append(_uuid.UUID(str(i)))
        except (ValueError, AttributeError, TypeError):
            continue
    return out


def enrich(db: Session, records: List[dict]) -> Dict[str, str]:
    """Attach a ``document`` preview to each record in place so an admin sees
    WHICH document was retrieved, not a bare UUID. Returns per-corpus notes for
    lookups that failed. Never raises — the ids alone are still useful, and a
    dead referent must not 500 the debugger."""
    ids_by_corpus: Dict[str, set] = {}
    for r in records:
        rid = r.get("id")
        if rid and rid != "None":
            ids_by_corpus.setdefault(r.get("corpus") or "unknown", set()).add(str(rid))

    docs: Dict[Tuple[str, str], dict] = {}
    notes: Dict[str, str] = {}

    rule_ids = _valid_uuids(ids_by_corpus.get("rules") or ())
    if rule_ids:
        try:
            rows = (
                db.query(Rule.id, Rule.rule_text, Rule.category, Rule.severity, Rule.product_line)
                .filter(Rule.id.in_(rule_ids))
                .all()
            )
            for row in rows:
                docs[("rules", str(row.id))] = {
                    "rule_text": (row.rule_text or "")[:_PREVIEW],
                    "category": row.category,
                    "severity": row.severity,
                    "product_line": row.product_line,
                }
        except Exception as e:  # noqa: BLE001 - degrade to bare ids
            notes["rules"] = f"rule lookup unavailable ({e.__class__.__name__})"

    # precedent_cases has no ORM model (it is a pgvector-managed table), so this
    # is raw SQL. ``id::text = ANY(:ids)`` sidesteps UUID casting on ids that
    # came from the rag_compliance_examples fallback index.
    prec_ids = sorted(ids_by_corpus.get("precedents") or ())
    if prec_ids:
        try:
            rows = db.execute(
                text(
                    "SELECT id::text AS id, issue_type, highlighted_span, source_file, "
                    "product_category, severity FROM precedent_cases "
                    "WHERE id::text = ANY(:ids)"
                ),
                {"ids": list(prec_ids)},
            ).mappings().all()
            for row in rows:
                docs[("precedents", str(row["id"]))] = {
                    "issue_type": row["issue_type"],
                    "highlighted_span": (row["highlighted_span"] or "")[:_PREVIEW],
                    "source_file": row["source_file"],
                    "product_category": row["product_category"],
                    "severity": row["severity"],
                }
        except Exception as e:  # noqa: BLE001 - degrade to bare ids
            notes["precedents"] = f"precedent lookup unavailable ({e.__class__.__name__})"

    for r in records:
        r["document"] = docs.get((r.get("corpus") or "unknown", str(r.get("id"))))
    return notes


# --- response shapes --------------------------------------------------------

def _run_head(run) -> dict:
    return {
        "run_id": str(getattr(run, "id", None)),
        "submission_id": str(getattr(run, "submission_id", None)),
        "run_number": getattr(run, "run_number", None),
        "run_status": getattr(run, "status", None),
        "degraded_reason": getattr(run, "degraded_reason", None),
        "started_at": _iso(getattr(run, "started_at", None)),
    }


def _no_data(run, reason: str) -> dict:
    return {"status": "no_retrieval_data", "reason": reason, **_run_head(run)}


def _totals(dbg: dict, records: List[dict]) -> dict:
    """Persisted totals vs what is actually inspectable. They differ because
    dispatch_node caps BOTH verdicts at 100 records per run
    (``_rejected[:100]`` / ``_accepted[:100]`` in graph/nodes.py) — say so
    instead of implying the sample is the whole population.

    The note used to claim rejections were persisted in full. They are not, and
    a consumer that believed it read the 100-row rejection sample as the whole
    rejection population sitting next to a rejected_total in the thousands."""
    total = dbg.get("candidates_total")
    rejected_total = dbg.get("rejected_total")
    recorded_rejected = sum(1 for r in records if r.get("verdict") == "rejected")
    return {
        "candidates_total": total,
        "rejected_total": rejected_total,
        "records_available": len(records),
        "truncated": bool(isinstance(total, int) and total > len(records)),
        "note": (
            "Rejections AND acceptances are each capped at 100 records per run. "
            "Every per-record count here is of that sample; only "
            "candidates_total and rejected_total count the whole run."
            if isinstance(total, int) and total > len(records)
            else None
        ),
        "recorded_rejected": recorded_rejected,
    }


def _story(run, dbg: dict, db: Session) -> dict:
    records = _records(dbg)
    notes = enrich(db, records)
    md = getattr(run, "run_metadata", None) or {}
    return {
        "status": "ok",
        **_run_head(run),
        "scope": dbg.get("scope"),
        "product_match": md.get("product_match"),
        "degraded": {
            k: md.get(k)
            for k in ("degraded", "rag_degraded", "disclosure_recall_degraded",
                      "analysis_failed_chunks")
            if md.get(k) not in (None, False)
        },
        "totals": _totals(dbg, records),
        "by_corpus_recorded": count_by_corpus(records),
        "candidates": group_by_corpus_tier(records),
        "enrichment_notes": notes or None,
    }


def _get_run(db: Session, run_id: str) -> AnalysisRun:
    try:
        run = db.get(AnalysisRun, run_id)
    except Exception:  # noqa: BLE001 - malformed uuid must 404, not 500
        run = None
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found.")
    return run


# --- routes -----------------------------------------------------------------

@router.get("/runs/{run_id}")
async def run_retrieval(
    run_id: str,
    db: Session = Depends(get_db),
    _actor=Depends(require("rules:write")),
):
    """Full retrieval story for one run: resolved scope, per-corpus
    accepted/rejected counts, and every recorded candidate grouped by corpus
    and tier with its score, scope value, verdict and reason."""
    run = _get_run(db, run_id)
    dbg, absent = extract_debug(run)
    if dbg is None:
        return _no_data(run, absent)
    return _story(run, dbg, db)


@router.get("/submissions/{submission_id}/latest")
async def latest_submission_retrieval(
    submission_id: str,
    db: Session = Depends(get_db),
    _actor=Depends(require("rules:write")),
):
    """Same as /runs/{run_id} for the submission's most recent run — reviewers
    think in submissions, not run ids."""
    try:
        run = (
            db.query(AnalysisRun)
            .filter(AnalysisRun.submission_id == submission_id)
            .order_by(AnalysisRun.run_number.desc(), AnalysisRun.started_at.desc())
            .first()
        )
    except Exception:  # noqa: BLE001 - malformed uuid must 404, not 500
        run = None
    if run is None:
        raise HTTPException(status_code=404, detail="No analysis run for this submission.")
    dbg, absent = extract_debug(run)
    if dbg is None:
        return _no_data(run, absent)
    return _story(run, dbg, db)


@router.get("/runs/{run_id}/rejected")
async def run_rejected(
    run_id: str,
    db: Session = Depends(get_db),
    _actor=Depends(require("rules:write")),
):
    """Only the rejected candidates, bucketed by reason code — "what did we
    wrongly exclude" is the question that drives corpus fixes."""
    run = _get_run(db, run_id)
    dbg, absent = extract_debug(run)
    if dbg is None:
        return _no_data(run, absent)

    rejected = [r for r in _records(dbg) if r.get("verdict") == "rejected"]
    notes = enrich(db, rejected)
    return {
        "status": "ok",
        **_run_head(run),
        "scope": dbg.get("scope"),
        "rejected_total": dbg.get("rejected_total"),
        "records_available": len(rejected),
        "by_reason": group_by_reason(rejected),
        "enrichment_notes": notes or None,
    }


# ===========================================================================
# Per-candidate trace (migration 0037, backend/app/services/rag/trace.py)
# ===========================================================================
#
# The three routes above read a 100-row SAMPLE out of run_metadata. The two
# below read `retrieval_candidates`, which is the whole per-chunk population:
# one row per candidate per (chunk, category) query, carrying the cosine leg,
# the ts_rank leg, the fusion rank, the applicability verdict, and the stage
# that ultimately decided its fate.
#
# Same honesty contract, different absence: a run with no rows here has no
# trace, which is NOT the same as "nothing was retrieved" — every run before
# migration 0037 is in that state and must say so.

_TRACE_COLUMNS = (
    "chunk_id, corpus, tier, category, candidate_id, retrieval_method, cosine, "
    "ts_rank, vector_rank, bm25_rank, fused_score, fused_rank, filters, "
    "scope_value, verdict, reason, final_status, deciding_stage"
)

# Only these can be filtered on, and each maps to a bound parameter — the
# request never contributes SQL text.
_TRACE_FILTERS = ("chunk_id", "corpus", "verdict", "final_status")

MAX_PAGE = 200


def trace_where(params: Dict[str, Any], *, skip: Optional[str] = None) -> str:
    """`AND ...` clause for the supplied filters. `skip` omits one dimension —
    used so the final_status facet still counts the buckets you are not
    currently looking at (a facet filtered by its own value is always 1 bucket,
    which is not a facet)."""
    parts = []
    for key in _TRACE_FILTERS:
        if key == skip or params.get(key) in (None, ""):
            continue
        parts.append(f" AND {key} = :{key}")
    return "".join(parts)


def facet_counts(rows: List[dict]) -> Dict[str, Dict[str, int]]:
    """(final_status, deciding_stage, n) group-by rows -> two flat facets."""
    by_status: Dict[str, int] = {}
    by_stage: Dict[str, int] = {}
    for r in rows:
        n = int(r.get("n") or 0)
        by_status[r.get("final_status") or "unknown"] = (
            by_status.get(r.get("final_status") or "unknown", 0) + n
        )
        by_stage[r.get("deciding_stage") or "unknown"] = (
            by_stage.get(r.get("deciding_stage") or "unknown", 0) + n
        )
    return {"final_status": by_status, "deciding_stage": by_stage}


def used_in_final_verdict(db: Session, run_id: str) -> Set[Tuple[str, str]]:
    """(corpus, candidate_id) pairs this run actually cited in a violation.

    Computed here, from the join, because the client has no access to the
    run's violations — and a client-side guess would silently show every
    accepted candidate as "used"."""
    try:
        rows = db.execute(
            text(
                "SELECT 'rules' AS corpus, rule_id::text AS id FROM violations "
                "WHERE analysis_run_id = CAST(:run_id AS UUID) AND rule_id IS NOT NULL "
                "UNION "
                "SELECT 'precedents' AS corpus, cited_precedent_id::text AS id FROM violations "
                "WHERE analysis_run_id = CAST(:run_id AS UUID) "
                "AND cited_precedent_id IS NOT NULL"
            ),
            {"run_id": str(run_id)},
        ).mappings().all()
        return {(r["corpus"], str(r["id"])) for r in rows}
    except Exception as e:  # noqa: BLE001 - a dead join must not 500 the page
        logger.warning(
            "used_in_final_verdict: violation join unavailable (%s)",
            e.__class__.__name__,
        )
        return set()


def chunk_rollup(rows: List[dict]) -> List[dict]:
    """(chunk_id, corpus, ...) group-by rows -> one entry per chunk, with the
    per-corpus split kept underneath."""
    out: Dict[Any, dict] = {}
    for r in rows:
        cid = r.get("chunk_id")
        entry = out.setdefault(cid, {
            "chunk_id": cid,
            "candidates": 0, "accepted": 0, "in_prompt": 0,
            "rejected_applicability": 0, "dropped_by_cap": 0,
            "top_fused_score": None,
            "by_corpus": {},
        })
        counts = {
            "candidates": int(r.get("candidates") or 0),
            "accepted": int(r.get("accepted") or 0),
            "in_prompt": int(r.get("in_prompt") or 0),
            "rejected_applicability": int(r.get("rejected_applicability") or 0),
            "dropped_by_cap": int(r.get("dropped_by_cap") or 0),
            "top_fused_score": (
                float(r["top_fused_score"]) if r.get("top_fused_score") is not None else None
            ),
        }
        for k in ("candidates", "accepted", "in_prompt", "rejected_applicability",
                  "dropped_by_cap"):
            entry[k] += counts[k]
        if counts["top_fused_score"] is not None:
            entry["top_fused_score"] = (
                counts["top_fused_score"] if entry["top_fused_score"] is None
                else max(entry["top_fused_score"], counts["top_fused_score"])
            )
        entry["by_corpus"][r.get("corpus") or "unknown"] = counts
    # Chunk ids are UUIDs, so there is no meaningful document order to sort by;
    # sort by weight instead, which puts the chunks worth curating on top. The
    # NULL bucket (the flat fallback set, which belongs to no chunk) sorts last.
    return sorted(
        out.values(),
        key=lambda e: (e["chunk_id"] is None, -e["candidates"], str(e["chunk_id"] or "")),
    )


_NO_TRACE = (
    "no per-candidate trace for this run — it ran before migration 0037 added "
    "retrieval_candidates, or the trace insert failed (it is deliberately "
    "fail-soft and never blocks an analysis). This is NOT 'nothing was "
    "retrieved': use the sampled verdict view above for what this run recorded."
)


def _trace_count(db: Session, run_id: str) -> int:
    return int(
        db.execute(
            text(
                "SELECT COUNT(*) AS n FROM retrieval_candidates "
                "WHERE run_id = CAST(:run_id AS UUID)"
            ),
            {"run_id": str(run_id)},
        ).mappings().first()["n"]
        or 0
    )


@router.get("/runs/{run_id}/candidates")
async def run_candidates(
    run_id: str,
    chunk_id: Optional[str] = None,
    corpus: Optional[str] = None,
    verdict: Optional[str] = None,
    final_status: Optional[str] = None,
    limit: int = Query(25, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _actor=Depends(require("rules:write")),
):
    """Every candidate this run considered, with the stage that decided it.

    One row answers, at a glance: which leg found it, how it scored on each,
    where it landed after fusion, what the applicability judge said and why,
    what its final fate was — and whether the run went on to cite it."""
    run = _get_run(db, run_id)
    try:
        total_rows = _trace_count(db, run_id)
    except Exception as e:  # noqa: BLE001 - table missing => no trace, not a 500
        return {**_no_data(run, _NO_TRACE), "detail": e.__class__.__name__}
    if not total_rows:
        return _no_data(run, _NO_TRACE)

    params: Dict[str, Any] = {
        "run_id": str(run_id), "chunk_id": chunk_id, "corpus": corpus,
        "verdict": verdict, "final_status": final_status,
    }
    where = "WHERE run_id = CAST(:run_id AS UUID)" + trace_where(params)
    bound = {k: v for k, v in params.items() if v not in (None, "")}

    matched = int(
        db.execute(
            text(f"SELECT COUNT(*) AS n FROM retrieval_candidates {where}"), bound
        ).mappings().first()["n"] or 0
    )
    rows = [
        dict(r)
        for r in db.execute(
            text(
                f"SELECT {_TRACE_COLUMNS} FROM retrieval_candidates {where} "
                # Stable, and the useful reading order: best-ranked first,
                # un-fused rows (the flat fallback set) last.
                "ORDER BY chunk_id NULLS LAST, corpus, fused_rank NULLS LAST, "
                "candidate_id LIMIT :limit OFFSET :offset"
            ),
            {**bound, "limit": limit, "offset": offset},
        ).mappings().all()
    ]

    facet_rows = [
        dict(r)
        for r in db.execute(
            text(
                "SELECT final_status, deciding_stage, COUNT(*) AS n "
                "FROM retrieval_candidates "
                "WHERE run_id = CAST(:run_id AS UUID)"
                + trace_where(params, skip="final_status")
                + " GROUP BY final_status, deciding_stage"
            ),
            {k: v for k, v in bound.items() if k != "final_status"},
        ).mappings().all()
    ]

    cited = used_in_final_verdict(db, run_id)
    for r in rows:
        # `id` mirrors the sampled-verdict shape so enrich() and the UI can
        # treat both candidate flavours the same way.
        r["id"] = r.get("candidate_id")
        r["used_in_final_verdict"] = (
            (r.get("corpus") or "", str(r.get("candidate_id"))) in cited
        )
    notes = enrich(db, rows)

    return {
        "status": "ok",
        **_run_head(run),
        "total_traced": total_rows,
        "matched": matched,
        "limit": limit,
        "offset": offset,
        "filters": {k: params.get(k) for k in _TRACE_FILTERS},
        "rows": rows,
        # Facets honour every filter EXCEPT final_status, so the buckets you are
        # not looking at still show their size.
        "facets": facet_counts(facet_rows),
        "enrichment_notes": notes or None,
    }


@router.get("/runs/{run_id}/chunks")
async def run_chunks(
    run_id: str,
    db: Session = Depends(get_db),
    _actor=Depends(require("rules:write")),
):
    """Per-chunk rollup of the candidate trace — where to drill in first."""
    run = _get_run(db, run_id)
    try:
        rows = [
            dict(r)
            for r in db.execute(
                text(
                    "SELECT chunk_id, corpus, COUNT(*) AS candidates, "
                    "COUNT(*) FILTER (WHERE verdict = 'accepted') AS accepted, "
                    "COUNT(*) FILTER (WHERE final_status = 'in_prompt') AS in_prompt, "
                    "COUNT(*) FILTER (WHERE final_status = 'rejected_applicability') "
                    "  AS rejected_applicability, "
                    "COUNT(*) FILTER (WHERE final_status = 'dropped_by_cap') "
                    "  AS dropped_by_cap, "
                    "MAX(fused_score) AS top_fused_score "
                    "FROM retrieval_candidates WHERE run_id = CAST(:run_id AS UUID) "
                    "GROUP BY chunk_id, corpus"
                ),
                {"run_id": str(run_id)},
            ).mappings().all()
        ]
    except Exception as e:  # noqa: BLE001 - table missing => no trace, not a 500
        return {**_no_data(run, _NO_TRACE), "detail": e.__class__.__name__}
    if not rows:
        return _no_data(run, _NO_TRACE)

    chunks = chunk_rollup(rows)
    return {
        "status": "ok",
        **_run_head(run),
        "chunks": chunks,
        "chunks_total": len(chunks),
        "candidates_total": sum(c["candidates"] for c in chunks),
    }
