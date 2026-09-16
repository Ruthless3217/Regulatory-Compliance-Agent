"""Repair the primary-UIN stamp on brochure vectors whose identity is PROVEN.

WHAT WENT WRONG
    brochure_parser took `uins[0]` — the first UIN in document order — as the
    plan UIN. Brochures list their riders early, so 14 of 49 ingested
    documents were stamped with a rider's UIN (five different products all
    carry 116A057V02), and product_docs_indexer copied that stamp onto every
    one of their vectors in rag_product_docs. Those 860 vectors cannot be
    reached by any plan-scoped retrieval; eTouch II (116N198V07) has zero
    retrievable passages because its 58 vectors say 116B056V01.

WHAT THIS CHANGES — AND WHAT IT NEVER TOUCHES
    rag_product_docs keeps identity as plain scalar columns beside the vector.
    Exactly ONE column changes on the vectors: `uin`. `product_name` is
    deliberately left alone — a BEFORE INSERT OR UPDATE trigger
    (rag_product_docs_tsv_trg) rebuilds `search_tsv` from text, section_path
    AND product_name, so touching the name would rewrite the lexical index.
    The parsed name on these rows ("eTouch II") is not wrong, only
    non-canonical, and the identity defect is the UIN.

    Not in the UPDATE, and PROVEN unchanged by a before/after fingerprint:
    `embedding`, `text`, `chunk_index`, `id`, `product_document_id`,
    `embedding_model`, `embedding_dim`, `product_name`, `search_tsv`,
    `updated_at`. The trigger still fires on the UPDATE, recomputes
    `search_tsv` from unchanged inputs, and produces the identical value —
    the fingerprint includes md5(search_tsv::text) to prove that rather than
    assume it. `updated_at` is DEFAULT now() with no ON UPDATE trigger, so a
    raw UPDATE does not move it; it is fingerprinted too.

    No embedding is regenerated, no chunk is rewritten, no document is
    re-parsed or re-ingested. Any fingerprint difference rolls the whole
    transaction back.

    product_documents.uin is corrected on the same 14 rows so the registry
    and the vectors agree.

THE DETERMINISM RULE — two independent signals must agree, or nothing happens
    S1  the document's product_name resolves to exactly ONE fact-card plan UIN
    S2  that same UIN is already present in the document's own uins[] array
    A document satisfying both is SAFE_METADATA_REPAIR. Anything else is
    refused: a name matching no card, or matching several, or a stored UIN
    that IS the name-derived one already. The forensic classification of
    2026-09-11 found 14 / 0 / 28 / 7 documents in those four classes; this
    script recomputes it live and STOPS if the repairable set is not exactly
    the 14 it expects, so a corpus change cannot silently widen the blast
    radius.

Idempotent — a document already carrying its plan UIN is CORRECT_AS_IS and is
never touched. After --apply the repairable set is empty and the script exits
0 with "nothing to repair"; it exits 2 only when the set is non-empty AND
differs from the reviewed 14 / 860.

Usage:
    python -m scripts.repair_product_doc_identity            # dry run (default)
    python -m scripts.repair_product_doc_identity --apply    # persist, in one txn
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
# The report IS the deliverable; the engine's statement echo is not part of it.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logger = logging.getLogger("repair_product_doc_identity")

CARDS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "product_fact_cards")

# The forensic classification this script must reproduce exactly. If the live
# corpus disagrees — a new ingest, a curated card, a deleted document — the
# repair is no longer the reviewed one and the script refuses to proceed.
EXPECTED_REPAIRABLE_DOCS = 14
EXPECTED_REPAIRABLE_VECTORS = 860

SAFE = "A_SAFE_METADATA_REPAIR"
AMBIGUOUS = "B_AMBIGUOUS_REQUIRES_CURATION"
CORRECT = "C_CORRECT_AS_IS"
INSUFFICIENT = "D_INSUFFICIENT_EVIDENCE"


@dataclass
class Decision:
    doc_id: str
    product_name: str
    stored_uin: Optional[str]
    proposed_uin: Optional[str]
    proposed_name: Optional[str]
    verdict: str
    evidence: str
    vectors: int
    confidence: str = "none"


@dataclass(frozen=True)
class RowFingerprint:
    """Everything that must be byte-identical before and after the repair.

    Only `uin` is allowed to differ between the two captures."""
    row_id: str
    doc_id: str
    chunk_index: int
    embedding_md5: str
    text_md5: str
    embedding_model: Optional[str]
    embedding_dim: Optional[int]
    product_name: Optional[str]
    search_tsv_md5: Optional[str]
    updated_at: Optional[str]


# --------------------------------------------------------------------------
# Fact-card name index (read-only, from disk)
# --------------------------------------------------------------------------


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = re.sub(r"\buin\b", " ", s)
    s = re.sub(r"^\s*bajaj\s+(life|allianz\s+life)\s*", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _load_card_index() -> tuple[Dict[str, Set[str]], Set[str], Dict[str, str]]:
    """(normalised name -> plan UINs, every plan UIN, uin -> canonical name)."""
    by_name: Dict[str, Set[str]] = defaultdict(set)
    plan: Set[str] = set()
    canonical: Dict[str, str] = {}
    for path in sorted(glob.glob(os.path.join(CARDS_DIR, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            card = json.load(fh)
        uin, name = card.get("uin"), card.get("product_name") or ""
        if not uin:
            continue
        plan.add(uin)
        by_name[_norm(name)].add(uin)
        # Several variant cards share a UIN (116L214V01 ×3); the canonical
        # display name is the shortest, i.e. the base product's.
        if uin not in canonical or len(name) < len(canonical[uin]):
            canonical[uin] = name
    return by_name, plan, canonical


def _match_name(doc_name: str, by_name: Dict[str, Set[str]]) -> Set[str]:
    n = _norm(doc_name)
    if not n:
        return set()
    if n in by_name:
        return set(by_name[n])
    return {
        u for key, uins in by_name.items()
        if key and (key.startswith(n) or n.startswith(key))
        for u in uins
    }


# --------------------------------------------------------------------------
# Classification (read-only)
# --------------------------------------------------------------------------


def classify(db: Session) -> List[Decision]:
    by_name, plan, canonical = _load_card_index()
    rows = db.execute(text(
        """
        SELECT pd.id::text AS id, pd.product_name, pd.uin, pd.uins, pd.status,
               (SELECT count(*) FROM rag_product_docs r
                 WHERE r.product_document_id = pd.id) AS vectors
          FROM product_documents pd
         ORDER BY pd.product_name, pd.id
        """
    )).mappings().all()

    decisions: List[Decision] = []
    for row in rows:
        uins = list(row["uins"] or [])
        stored, name = row["uin"], row["product_name"] or ""
        vectors = int(row["vectors"] or 0)
        matched = _match_name(name, by_name)
        corroborated = {u for u in matched if u in uins}

        if row["status"] == "quarantined" or stored is None:
            decisions.append(Decision(row["id"], name, stored, None, None,
                                      INSUFFICIENT, "quarantined / no UIN parsed",
                                      vectors))
        elif len(corroborated) == 1:
            target = next(iter(corroborated))
            if target == stored:
                decisions.append(Decision(row["id"], name, stored, stored,
                                          canonical[stored], CORRECT,
                                          "stored uin == name-derived plan uin",
                                          vectors, "n/a"))
            else:
                decisions.append(Decision(
                    row["id"], name, stored, target, canonical[target], SAFE,
                    f"S1 name {name!r} -> {target}; S2 {target} is in this "
                    f"document's own uins[] {uins}; stored {stored} is not the "
                    f"plan", vectors, "deterministic (S1 AND S2)"))
        elif len(corroborated) > 1:
            decisions.append(Decision(row["id"], name, stored, None, None,
                                      AMBIGUOUS,
                                      f"name matches >1 plan uin: {sorted(corroborated)}",
                                      vectors))
        elif stored in plan and stored in uins:
            decisions.append(Decision(row["id"], name, stored, stored,
                                      canonical.get(stored, name), CORRECT,
                                      "no name match, but stored uin is a plan "
                                      "uin present in uins[]", vectors, "n/a"))
        else:
            decisions.append(Decision(
                row["id"], name, stored, None, None, INSUFFICIENT,
                f"name {name!r} matches no card (-> {sorted(matched) or 'none'}); "
                f"uins={uins}", vectors))
    return decisions


# --------------------------------------------------------------------------
# Fingerprints (read-only)
# --------------------------------------------------------------------------


def fingerprint_rows(db: Session, doc_ids: List[str]) -> Dict[str, RowFingerprint]:
    if not doc_ids:
        return {}
    rows = db.execute(text(
        """
        SELECT id::text AS id, product_document_id::text AS doc_id, chunk_index,
               md5(embedding::text) AS embedding_md5, md5(text) AS text_md5,
               embedding_model, embedding_dim, product_name,
               md5(search_tsv::text) AS search_tsv_md5, updated_at::text AS updated_at
          FROM rag_product_docs
         WHERE product_document_id = ANY(CAST(:ids AS uuid[]))
        """
    ), {"ids": doc_ids}).mappings().all()
    return {
        r["id"]: RowFingerprint(r["id"], r["doc_id"], r["chunk_index"],
                                r["embedding_md5"], r["text_md5"],
                                r["embedding_model"], r["embedding_dim"],
                                r["product_name"], r["search_tsv_md5"],
                                r["updated_at"])
        for r in rows
    }


def _assert_identical(before: Dict[str, RowFingerprint],
                      after: Dict[str, RowFingerprint]) -> None:
    if set(before) != set(after):
        raise RuntimeError(
            f"row set changed: {len(before)} before, {len(after)} after"
        )
    for row_id, b in before.items():
        a = after[row_id]
        # Frozen dataclass equality: every field, including product_name,
        # search_tsv and updated_at. Nothing but `uin` may differ, and `uin`
        # is deliberately not part of the fingerprint.
        if a != b:
            raise RuntimeError(f"row {row_id} changed beyond its uin: {b} -> {a}")


# --------------------------------------------------------------------------
# The mutation — the ONLY writes this script performs
# --------------------------------------------------------------------------

# `uin` only. Guarded on the value the dry run saw, so a row that changed
# underneath us updates nothing and the rowcount check below aborts.
_UPDATE_VECTORS = text(
    """
    UPDATE rag_product_docs
       SET uin = :uin
     WHERE product_document_id = CAST(:doc_id AS uuid)
       AND uin IS NOT DISTINCT FROM :stored_uin
    """
)
_UPDATE_DOCUMENT = text(
    """
    UPDATE product_documents
       SET uin = :uin
     WHERE id = CAST(:doc_id AS uuid) AND uin = :stored_uin
    """
)


def apply_repairs(db: Session, repairs: List[Decision]) -> int:
    """Apply inside the caller's transaction. Returns vectors updated."""
    updated = 0
    for d in repairs:
        if d.verdict != SAFE:
            raise RuntimeError(f"refusing to touch non-SAFE document {d.doc_id}")
        result = db.execute(_UPDATE_VECTORS, {
            "uin": d.proposed_uin, "doc_id": d.doc_id, "stored_uin": d.stored_uin,
        })
        if result.rowcount != d.vectors:
            raise RuntimeError(
                f"{d.doc_id}: expected {d.vectors} vectors, updated {result.rowcount}"
            )
        updated += result.rowcount
        doc = db.execute(_UPDATE_DOCUMENT, {
            "uin": d.proposed_uin, "doc_id": d.doc_id, "stored_uin": d.stored_uin,
        })
        if doc.rowcount != 1:
            raise RuntimeError(f"{d.doc_id}: product_documents row moved underneath us")
    return updated


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _report(decisions: List[Decision]) -> None:
    by_verdict: Dict[str, List[Decision]] = defaultdict(list)
    for d in decisions:
        by_verdict[d.verdict].append(d)
    logger.info("documents: %d   vectors: %d",
                len(decisions), sum(d.vectors for d in decisions))
    for verdict in (SAFE, AMBIGUOUS, CORRECT, INSUFFICIENT):
        rows = by_verdict.get(verdict, [])
        logger.info("")
        logger.info("%s   docs=%d  vectors=%d", verdict, len(rows),
                    sum(d.vectors for d in rows))
        for d in sorted(rows, key=lambda x: -x.vectors):
            if verdict == SAFE:
                logger.info("  %-36s %s  %-10s -> %-10s  vec=%-4d  %s",
                            d.doc_id, d.product_name[:38].ljust(38),
                            d.stored_uin, d.proposed_uin, d.vectors, d.confidence)
                logger.info("      %s", d.evidence)
            else:
                logger.info("  %-36s %s  %-10s  vec=%-4d",
                            d.doc_id, d.product_name[:38].ljust(38),
                            d.stored_uin or "<NULL>", d.vectors)
                if verdict != CORRECT:
                    logger.info("      %s", d.evidence)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="persist the repair (default: dry run, no writes)")
    args = parser.parse_args()

    db: Session = SessionLocal()
    try:
        decisions = classify(db)
        _report(decisions)

        repairs = [d for d in decisions if d.verdict == SAFE]
        n_docs, n_vecs = len(repairs), sum(d.vectors for d in repairs)
        logger.info("")
        logger.info("REPAIRABLE: %d documents / %d vectors  (expected %d / %d)",
                    n_docs, n_vecs, EXPECTED_REPAIRABLE_DOCS, EXPECTED_REPAIRABLE_VECTORS)
        refused = [d for d in decisions if d.verdict in (AMBIGUOUS, INSUFFICIENT)]
        logger.info("REFUSED (never touched): %d documents / %d vectors",
                    len(refused), sum(d.vectors for d in refused))

        if not repairs:
            logger.info("")
            logger.info("nothing to repair - every document with a name-derivable "
                        "plan UIN already carries it. No writes.")
            return 0
        if (n_docs, n_vecs) != (EXPECTED_REPAIRABLE_DOCS, EXPECTED_REPAIRABLE_VECTORS):
            logger.error(
                "STOP: the live corpus does not match the reviewed classification "
                "(%d/%d vs expected %d/%d). Re-run the forensic audit before "
                "repairing.", n_docs, n_vecs,
                EXPECTED_REPAIRABLE_DOCS, EXPECTED_REPAIRABLE_VECTORS,
            )
            return 2

        doc_ids = [d.doc_id for d in repairs]
        before = fingerprint_rows(db, doc_ids)
        logger.info("")
        logger.info("identity fingerprint captured for %d vectors "
                    "(md5(embedding), md5(text), chunk_index, doc id, model, dim)",
                    len(before))
        if len(before) != n_vecs:
            logger.error("STOP: fingerprinted %d rows but expected %d", len(before), n_vecs)
            return 2

        if not args.apply:
            logger.info("")
            logger.info("DRY RUN - no writes performed. Re-run with --apply to persist.")
            logger.info("--apply would execute, per document, inside ONE transaction:")
            logger.info("  UPDATE rag_product_docs SET uin=:plan_uin")
            logger.info("   WHERE product_document_id=:doc_id AND uin IS NOT DISTINCT FROM :stored_uin;")
            logger.info("  UPDATE product_documents SET uin=:plan_uin")
            logger.info("   WHERE id=:doc_id AND uin=:stored_uin;")
            logger.info("then re-fingerprint (embedding, text, chunk_index, ids, model, dim,")
            logger.info("product_name, search_tsv, updated_at) and ROLLBACK on any difference.")
            return 0

        logger.info("")
        logger.info("APPLYING in one transaction ...")
        try:
            updated = apply_repairs(db, repairs)
            after = fingerprint_rows(db, doc_ids)
            _assert_identical(before, after)
        except Exception as exc:
            db.rollback()
            logger.error("ROLLED BACK: %s", exc)
            return 1
        db.commit()
        logger.info("committed: %d vectors across %d documents re-stamped; "
                    "embedding, text, chunk_index, ids, product_name, search_tsv "
                    "and updated_at verified identical.", updated, n_docs)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
