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

Gated on ``rules:write`` — the corpus-curation scope (admin + super_admin,
never a plain grader).
"""
from __future__ import annotations

import uuid as _uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.dependencies import require
from app.models.analysis_run import AnalysisRun
from app.models.rule import Rule
from app.api.routes.admin_console import _iso

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
