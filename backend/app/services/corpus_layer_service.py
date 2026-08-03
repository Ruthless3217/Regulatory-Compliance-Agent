"""Corpus layers — create / enable / disable / purge an ingested contribution.

The point of a layer is that turning the corpus off is NOT the same operation as
throwing it away:

- ``update_layer(enabled=False)``  — one boolean UPDATE. Retrieval stops seeing
  the layer's precedents on the very next query (the store joins against
  ``corpus_layers`` live, there is no cache). Re-enabling costs another boolean;
  nothing is ever re-embedded, because the embeddings never moved.
- ``purge_layer()``                — irreversible DELETE of the layer's rows.
  ``precedent_cases`` *is* the vector table (the embedding is a column on the
  row), so deleting the row deletes the vector. There is no second index to
  clean up. Always logged at WARNING with the row count.

``precedent_cases`` has no SQLAlchemy model in this repo — it is created and
queried as raw SQL — so everything touching it here is raw SQL too, with bound
parameters only.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.corpus_layer import CORPUS_LAYER_KINDS, CorpusLayer

logger = logging.getLogger(__name__)

# Columns surfaced by list_items — enough for an admin to judge "is this
# contribution any good?" without dragging the embedding across the wire.
_ITEM_COLUMNS = (
    "id", "highlighted_span", "reviewer_comment", "issue_type", "severity",
    "product_category", "ticket", "source_file", "occurrence_count",
)


class CorpusLayerError(ValueError):
    """Bad input for a layer operation (unknown kind, duplicate name, ...)."""


# ---------------------------------------------------------------- helpers ---

def _refresh_count(db: Session, layer_id) -> int:
    """Re-sync ``item_count`` from the real row count. Cheap; the index on
    ``source_layer_id`` makes it a lookup, not a scan."""
    n = count_items(db, layer_id)
    db.execute(
        text("UPDATE corpus_layers SET item_count = :n, updated_at = NOW() WHERE id = CAST(:id AS UUID)"),
        {"n": n, "id": str(layer_id)},
    )
    return n


def _as_dict(layer: CorpusLayer, item_count: Optional[int] = None) -> Dict[str, Any]:
    return {
        "id": str(layer.id),
        "name": layer.name,
        "kind": layer.kind,
        "description": layer.description,
        "enabled": bool(layer.enabled),
        "source_ref": layer.source_ref,
        "ingested_by": str(layer.ingested_by) if layer.ingested_by else None,
        "item_count": int(item_count if item_count is not None else (layer.item_count or 0)),
        "created_at": layer.created_at.isoformat() if layer.created_at else None,
        "updated_at": layer.updated_at.isoformat() if layer.updated_at else None,
    }


# ------------------------------------------------------------------ reads ---

def get_layer(db: Session, layer_id) -> Optional[CorpusLayer]:
    try:
        pk = uuid.UUID(str(layer_id))
    except (ValueError, AttributeError, TypeError):
        return None  # a non-UUID path param is a 404, not a 500
    return db.get(CorpusLayer, pk)


def count_items(db: Session, layer_id) -> int:
    return int(
        db.execute(
            text("SELECT COUNT(*) FROM precedent_cases WHERE source_layer_id = CAST(:id AS UUID)"),
            {"id": str(layer_id)},
        ).scalar()
        or 0
    )


def count_unlayered(db: Session) -> int:
    """Precedents belonging to no layer — the pre-0030 corpus. These are always
    retrieved and cannot be switched off here; they have no provenance to switch."""
    return int(
        db.execute(
            text("SELECT COUNT(*) FROM precedent_cases WHERE source_layer_id IS NULL")
        ).scalar()
        or 0
    )


def list_layers(db: Session) -> List[Dict[str, Any]]:
    """All layers, newest first, with LIVE item counts.

    The count is computed rather than read off ``item_count`` so a layer whose
    rows were attached outside this service still reports the truth.
    """
    layers = db.query(CorpusLayer).order_by(CorpusLayer.created_at.desc()).all()
    counts = dict(
        db.execute(
            text(
                "SELECT source_layer_id, COUNT(*) FROM precedent_cases "
                "WHERE source_layer_id IS NOT NULL GROUP BY source_layer_id"
            )
        ).all()
    )
    return [_as_dict(l, int(counts.get(l.id, 0))) for l in layers]


def list_items(db: Session, layer_id, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """Paginated precedents belonging to a layer."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    rows = db.execute(
        text(
            f"SELECT {', '.join(_ITEM_COLUMNS)} FROM precedent_cases "
            "WHERE source_layer_id = CAST(:id AS UUID) ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
        ),
        {"id": str(layer_id), "limit": limit, "offset": offset},
    ).all()
    return {
        "total": count_items(db, layer_id),
        "limit": limit,
        "offset": offset,
        "items": [
            {c: (str(v) if hasattr(v, "hex") else v) for c, v in zip(_ITEM_COLUMNS, r)}
            for r in rows
        ],
    }


# ----------------------------------------------------------------- writes ---

def create_layer(
    db: Session,
    *,
    name: str,
    kind: str,
    description: Optional[str] = None,
    source_ref: Optional[str] = None,
    ingested_by=None,
    claim: bool = True,
) -> Dict[str, Any]:
    """Register a contribution.

    When ``claim`` and ``source_ref`` are set, precedents whose ``source_file``
    equals ``source_ref`` and that belong to no layer yet are adopted into it.
    That is what gives the 2,440 pre-existing, layer-less precedents a way into
    this system without re-ingesting or re-embedding them. Rows already owned by
    another layer are never stolen.
    """
    name = (name or "").strip()
    if not name:
        raise CorpusLayerError("name is required.")
    if kind not in CORPUS_LAYER_KINDS:
        raise CorpusLayerError(f"Unknown kind '{kind}'. Expected one of {sorted(CORPUS_LAYER_KINDS)}.")
    if db.query(CorpusLayer).filter(CorpusLayer.name == name).first() is not None:
        raise CorpusLayerError(f"A layer named '{name}' already exists.")

    layer = CorpusLayer(
        name=name,
        kind=kind,
        description=description,
        source_ref=source_ref,
        ingested_by=ingested_by,
        enabled=True,
        item_count=0,
    )
    db.add(layer)
    db.flush()  # need layer.id for the claim below

    claimed = 0
    if claim and source_ref:
        claimed = db.execute(
            text(
                "UPDATE precedent_cases SET source_layer_id = CAST(:lid AS UUID) "
                "WHERE source_file = :ref AND source_layer_id IS NULL"
            ),
            {"lid": str(layer.id), "ref": source_ref},
        ).rowcount or 0
        _refresh_count(db, layer.id)

    db.commit()
    logger.info(
        "corpus layer created: %s (%s) id=%s claimed=%d source_ref=%s",
        name, kind, layer.id, claimed, source_ref,
    )
    db.refresh(layer)
    return _as_dict(layer, claimed)


def update_layer(
    db: Session,
    layer_id,
    *,
    enabled: Optional[bool] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Flip the switch and/or re-describe. Enabling/disabling is O(1) and does
    not touch a single embedding."""
    layer = get_layer(db, layer_id)
    if layer is None:
        raise LookupError("Layer not found.")
    if enabled is not None and bool(enabled) != bool(layer.enabled):
        layer.enabled = bool(enabled)
        logger.info("corpus layer %s (%s) %s", layer.id, layer.name,
                    "ENABLED" if layer.enabled else "DISABLED")
    if description is not None:
        layer.description = description
    db.commit()
    db.refresh(layer)
    return _as_dict(layer, count_items(db, layer_id))


def purge_layer(db: Session, layer_id) -> int:
    """DELETE every precedent belonging to the layer. Returns the row count.

    Irreversible. ``precedent_cases`` holds the embedding as a column, so the
    row delete IS the vector delete — there is no separate index to sweep.
    """
    layer = get_layer(db, layer_id)
    if layer is None:
        raise LookupError("Layer not found.")
    deleted = db.execute(
        text("DELETE FROM precedent_cases WHERE source_layer_id = CAST(:id AS UUID)"),
        {"id": str(layer_id)},
    ).rowcount or 0
    _refresh_count(db, layer_id)
    db.commit()
    logger.warning(
        "corpus layer PURGED: %s (%s) — %d precedent rows deleted (irreversible)",
        layer_id, layer.name, deleted,
    )
    return deleted


def delete_layer(db: Session, layer_id, *, purge: bool = False) -> Dict[str, Any]:
    """Remove the layer record.

    ``purge=False`` (default) keeps the precedents and orphans them back to
    NULL — the FK is ON DELETE SET NULL, so they revert to always-retrieved,
    exactly as they were before layers existed. ``purge=True`` deletes them.
    """
    layer = get_layer(db, layer_id)
    if layer is None:
        raise LookupError("Layer not found.")
    name = layer.name
    deleted = purge_layer(db, layer_id) if purge else 0
    orphaned = 0 if purge else count_items(db, layer_id)
    db.delete(layer)
    db.commit()
    logger.warning(
        "corpus layer DELETED: %s (%s) purge=%s precedents_deleted=%d precedents_orphaned=%d",
        layer_id, name, purge, deleted, orphaned,
    )
    return {
        "id": str(layer_id),
        "name": name,
        "purged": bool(purge),
        "precedents_deleted": deleted,
        "precedents_orphaned": orphaned,
    }


# --------------------------------------------------- per-document curation ---
#
# Layer-level enable/purge answers "is this whole contribution any good?".
# Curation needs the finer grain: a corpus is kept current one source document
# at a time. Deleting the row deletes the vector with it — `precedent_cases`
# stores the embedding as a column, so there is no second index to sweep.

def list_documents(db: Session, layer_id=None) -> List[Dict[str, Any]]:
    """Source documents in the corpus, with their row counts.

    The admin thinks in documents ("drop the 2019 brochure"), not in the
    individual reviewer comments each one produced.

    ``layer_id=None`` covers the WHOLE corpus. That is the important case, not a
    convenience: a corpus ingested before layers existed has every row at
    ``source_layer_id IS NULL``, so layer-scoped curation can reach none of it.
    Documents are the primary grain here; layers are a grouping on top.
    """
    scoped = layer_id is not None
    if scoped and get_layer(db, layer_id) is None:
        raise LookupError("Layer not found.")
    rows = db.execute(
        text(
            f"""
            SELECT COALESCE(source_file, '') AS source_file,
                   COUNT(*)                  AS precedent_count,
                   MAX(updated_at)           AS last_updated
              FROM precedent_cases
             {"WHERE source_layer_id = CAST(:id AS UUID)" if scoped else ""}
             GROUP BY COALESCE(source_file, '')
             ORDER BY precedent_count DESC
            """
        ),
        {"id": str(layer_id)} if scoped else {},
    ).mappings().all()
    return [
        {
            "source_file": r["source_file"] or None,
            "precedent_count": int(r["precedent_count"]),
            "last_updated": r["last_updated"].isoformat() if r["last_updated"] else None,
        }
        for r in rows
    ]


def delete_document(db: Session, layer_id, source_file: str) -> int:
    """Remove one source document's precedents. Irreversible.

    With a ``layer_id`` the delete is scoped to that layer, because the same
    file name may legitimately appear in another contribution and a curator
    acting on one layer must not reach into another.

    With ``layer_id=None`` it removes that document from the whole corpus. That
    is the only way to curate rows ingested before layers existed, which is most
    of a mature corpus.
    """
    scoped = layer_id is not None
    if scoped and get_layer(db, layer_id) is None:
        raise LookupError("Layer not found.")
    name = (source_file or "").strip()
    if not name:
        raise CorpusLayerError("source_file is required.")
    deleted = db.execute(
        text(
            "DELETE FROM precedent_cases WHERE source_file = :src"
            + (" AND source_layer_id = CAST(:id AS UUID)" if scoped else "")
        ),
        {"src": name, **({"id": str(layer_id)} if scoped else {})},
    ).rowcount or 0
    if not deleted:
        raise LookupError("No precedents for that source document.")
    if scoped:
        _refresh_count(db, layer_id)
    db.commit()
    logger.warning(
        "corpus document PURGED: layer=%s source_file=%r — %d precedent rows "
        "deleted (irreversible; embeddings removed with the rows)",
        layer_id if scoped else "ALL (corpus-wide)", name, deleted,
    )
    return deleted


def delete_item(db: Session, layer_id, item_id) -> None:
    """Remove one precedent from the layer. Irreversible."""
    if get_layer(db, layer_id) is None:
        raise LookupError("Layer not found.")
    deleted = db.execute(
        text(
            "DELETE FROM precedent_cases"
            " WHERE source_layer_id = CAST(:lid AS UUID) AND id = CAST(:iid AS UUID)"
        ),
        {"lid": str(layer_id), "iid": str(item_id)},
    ).rowcount or 0
    if not deleted:
        raise LookupError("Precedent not found in this layer.")
    _refresh_count(db, layer_id)
    db.commit()
    logger.warning(
        "corpus precedent PURGED: layer=%s id=%s (irreversible)", layer_id, item_id
    )


async def add_precedents(db: Session, layer_id, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Embed and insert precedents, then stamp them into this layer.

    Embedding happens inside ``upsert_precedents`` (the same path the bulk
    ingest uses), so a document added here is retrievable on the next query
    with no separate re-index step. The layer stamp is a follow-up UPDATE
    because the store's INSERT does not carry ``source_layer_id`` — the same
    two-step ``scripts/ingest_precedent_cases.py`` performs.

    Raises before writing anything if the layer is unknown, so a typo'd id
    cannot leave newly-embedded rows orphaned outside every layer.
    """
    if get_layer(db, layer_id) is None:
        raise LookupError("Layer not found.")
    if not rows:
        return {"indexed": 0, "assigned": 0}

    from app.services.rag.indexers.precedent_indexer import upsert_precedents

    ids = []
    for row in rows:
        row.setdefault("id", str(uuid.uuid4()))
        ids.append(str(row["id"]))
    indexed = await upsert_precedents(rows)
    assigned = db.execute(
        text(
            "UPDATE precedent_cases SET source_layer_id = CAST(:lid AS UUID)"
            " WHERE id = ANY(CAST(:ids AS UUID[]))"
        ),
        {"lid": str(layer_id), "ids": ids},
    ).rowcount or 0
    _refresh_count(db, layer_id)
    db.commit()
    logger.info(
        "corpus layer %s: %d precedents embedded, %d assigned", layer_id, indexed, assigned
    )
    return {"indexed": indexed, "assigned": assigned}
