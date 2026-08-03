"""Admin corpus-layer API — see, switch off, and remove ingested contributions.

Every route is gated by ``rules:write``, the existing knowledge-base mutation
scope (``auth/permissions.py``: admin + super_admin, never plain ``user``).
Corpus layers are knowledge-base content, so no new scope was minted for them.

Mutations are audited through the same ``services.observability.audit`` ledger
the super-admin console uses. A purge is the one irreversible operation here, so
it is audited with the exact row count it destroyed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require
from app.database import get_db
from app.models.corpus_layer import CORPUS_LAYER_KINDS
from app.services import corpus_layer_service as svc
from app.services.observability import audit
from app.services.rag.errors import RAGIndexingFailed

router = APIRouter(prefix="/admin/corpus", tags=["Admin Corpus"])

# Every route needs the same scope; declaring it once keeps it impossible to
# add a route here and forget the gate.
_ADMIN = Depends(require("rules:write"))


class CreateLayerIn(BaseModel):
    name: str
    kind: str
    description: Optional[str] = None
    source_ref: Optional[str] = None
    # Adopt existing, layer-less precedents whose source_file == source_ref.
    claim: bool = True


class UpdateLayerIn(BaseModel):
    enabled: Optional[bool] = None
    description: Optional[str] = None


@router.get("/layers")
async def list_layers(db: Session = Depends(get_db), _actor=_ADMIN):
    """All layers with live item counts, plus how many precedents belong to no
    layer at all (the pre-0030 corpus, which is always retrieved)."""
    return {
        "layers": svc.list_layers(db),
        "unlayered_count": svc.count_unlayered(db),
        "kinds": sorted(CORPUS_LAYER_KINDS),
    }


@router.post("/layers", status_code=201)
async def create_layer(
    body: CreateLayerIn,
    request: Request,
    db: Session = Depends(get_db),
    actor=_ADMIN,
):
    try:
        layer = svc.create_layer(
            db,
            name=body.name,
            kind=body.kind,
            description=body.description,
            source_ref=body.source_ref,
            ingested_by=getattr(actor, "id", None),
            claim=body.claim,
        )
    except svc.CorpusLayerError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await audit.record(
        "corpus_layer_created",
        actor=actor,
        request=request,
        target_type="corpus_layer",
        target_id=layer["id"],
        after={"name": layer["name"], "kind": layer["kind"],
               "source_ref": layer["source_ref"], "claimed": layer["item_count"]},
    )
    return layer


@router.patch("/layers/{layer_id}")
async def update_layer(
    layer_id: str,
    body: UpdateLayerIn,
    request: Request,
    db: Session = Depends(get_db),
    actor=_ADMIN,
):
    """Enable / disable / re-describe. Disabling hides the layer's precedents
    from retrieval on the next query and costs no re-embedding."""
    before = svc.get_layer(db, layer_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Layer not found.")
    was_enabled = bool(before.enabled)

    layer = svc.update_layer(
        db, layer_id, enabled=body.enabled, description=body.description
    )

    if body.enabled is not None and bool(body.enabled) != was_enabled:
        await audit.record(
            "corpus_layer_enabled" if layer["enabled"] else "corpus_layer_disabled",
            actor=actor,
            request=request,
            target_type="corpus_layer",
            target_id=layer["id"],
            before={"enabled": was_enabled},
            after={"enabled": layer["enabled"], "item_count": layer["item_count"]},
        )
    return layer


@router.delete("/layers/{layer_id}")
async def delete_layer(
    layer_id: str,
    request: Request,
    purge: bool = Query(False, description="Also DELETE the layer's precedents (irreversible)."),
    db: Session = Depends(get_db),
    actor=_ADMIN,
):
    """Remove the layer. ``purge=false`` orphans its precedents back to NULL
    (always-retrieved, as before layers existed); ``purge=true`` deletes them and
    their embeddings."""
    try:
        result = svc.delete_layer(db, layer_id, purge=purge)
    except LookupError:
        raise HTTPException(status_code=404, detail="Layer not found.")

    await audit.record(
        "corpus_layer_purged" if purge else "corpus_layer_deleted",
        actor=actor,
        request=request,
        target_type="corpus_layer",
        target_id=result["id"],
        before={"name": result["name"]},
        after={"purged": result["purged"],
               "precedents_deleted": result["precedents_deleted"],
               "precedents_orphaned": result["precedents_orphaned"]},
    )
    return result


@router.get("/layers/{layer_id}/items")
async def list_layer_items(
    layer_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _actor=_ADMIN,
):
    """Paginated precedents contributed by this layer."""
    if svc.get_layer(db, layer_id) is None:
        raise HTTPException(status_code=404, detail="Layer not found.")
    return svc.list_items(db, layer_id, limit=limit, offset=offset)


class AddPrecedentsIn(BaseModel):
    # Rows in the same shape the bulk ingest builds. Embedding happens
    # server-side, so the caller never supplies a vector.
    precedents: List[Dict[str, Any]]


@router.get("/layers/{layer_id}/documents")
async def list_layer_documents(
    layer_id: str,
    db: Session = Depends(get_db),
    _actor=_ADMIN,
):
    """Source documents in this layer, with the precedent count each produced."""
    try:
        return {"documents": svc.list_documents(db, layer_id)}
    except LookupError:
        raise HTTPException(status_code=404, detail="Layer not found.")


@router.delete("/layers/{layer_id}/documents")
async def delete_layer_document(
    layer_id: str,
    request: Request,
    source_file: str = Query(..., description="Exact source_file to remove from this layer."),
    db: Session = Depends(get_db),
    actor=_ADMIN,
):
    """Remove one source document's precedents. Irreversible.

    The embedding is a column on the deleted row, so this removes the document
    from retrieval in the same statement — there is no separate re-index.
    """
    try:
        deleted = svc.delete_document(db, layer_id, source_file)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except svc.CorpusLayerError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await audit.record(
        "corpus_document_purged",
        actor=actor,
        request=request,
        target_type="corpus_layer",
        target_id=str(layer_id),
        before={"source_file": source_file},
        after={"precedents_deleted": deleted},
    )
    return {"layer_id": str(layer_id), "source_file": source_file, "precedents_deleted": deleted}


@router.delete("/layers/{layer_id}/items/{item_id}")
async def delete_layer_item(
    layer_id: str,
    item_id: str,
    request: Request,
    db: Session = Depends(get_db),
    actor=_ADMIN,
):
    """Remove one precedent from this layer. Irreversible."""
    try:
        svc.delete_item(db, layer_id, item_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await audit.record(
        "corpus_precedent_purged",
        actor=actor,
        request=request,
        target_type="corpus_layer",
        target_id=str(layer_id),
        before={"precedent_id": item_id},
        after={"precedents_deleted": 1},
    )
    return {"layer_id": str(layer_id), "precedent_id": item_id, "deleted": 1}


@router.post("/layers/{layer_id}/items", status_code=201)
async def add_layer_items(
    layer_id: str,
    payload: AddPrecedentsIn,
    request: Request,
    db: Session = Depends(get_db),
    actor=_ADMIN,
):
    """Embed and add precedents to this layer.

    Retrievable on the next query: embedding happens inline through the same
    indexer the bulk ingest uses, so there is no separate re-index step.
    """
    if not payload.precedents:
        raise HTTPException(status_code=400, detail="No precedents supplied.")
    try:
        result = await svc.add_precedents(db, layer_id, payload.precedents)
    except LookupError:
        raise HTTPException(status_code=404, detail="Layer not found.")
    except RAGIndexingFailed as e:
        # Nothing was stamped into the layer, so a failed embed leaves no
        # half-added document behind.
        raise HTTPException(status_code=502, detail=f"Embedding failed: {e}")

    await audit.record(
        "corpus_precedents_added",
        actor=actor,
        request=request,
        target_type="corpus_layer",
        target_id=str(layer_id),
        after=result,
    )
    return result


# ------------------------------------------------------ corpus-wide documents
#
# The layer-scoped routes above cannot reach rows ingested before layers
# existed, and in a mature corpus that is most of them (source_layer_id IS
# NULL). Documents are the grain an admin curates in, so they get a top-level
# surface that does not require a layer to exist first.

@router.get("/documents")
async def list_corpus_documents(db: Session = Depends(get_db), _actor=_ADMIN):
    """Every source document in the precedent corpus, layered or not."""
    return {"documents": svc.list_documents(db, None)}


@router.delete("/documents")
async def delete_corpus_document(
    request: Request,
    source_file: str = Query(..., description="Exact source_file to remove from the corpus."),
    db: Session = Depends(get_db),
    actor=_ADMIN,
):
    """Remove one source document from the WHOLE corpus. Irreversible.

    Deletes across every layer and the unlayered rows alike. The embedding is a
    column on each deleted row, so the document leaves retrieval in the same
    statement — there is no separate re-index.
    """
    try:
        deleted = svc.delete_document(db, None, source_file)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except svc.CorpusLayerError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await audit.record(
        "corpus_document_purged",
        actor=actor,
        request=request,
        target_type="corpus",
        target_id=source_file,
        before={"source_file": source_file, "scope": "corpus-wide"},
        after={"precedents_deleted": deleted},
    )
    return {"source_file": source_file, "precedents_deleted": deleted}
