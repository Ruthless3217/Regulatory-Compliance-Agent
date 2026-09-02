"""
Rules Management API Routes

Handles CRUD for compliance rules and AI-based rule generation from documents.
"""
import logging
import os
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from typing import Optional, List

from app.api.rate_limit import llm_rate_limit
from app.database import get_db
from app.models.rule import Rule
from app.services.rule_generator_service import rule_generator_service
from app.services.rag.indexers.rules_indexer import (
    delete_rule as rag_delete_rule,
    upsert_rule as rag_upsert_rule,
)
from app.schemas.rule import RuleCreate, RuleResponse
from app.auth.dependencies import require

logger = logging.getLogger(__name__)

# Document types we accept for rule generation. The stored filename is built
# as {uuid}{safe_ext}, so a malicious file.filename (traversal, null bytes)
# cannot influence the write path (audit H9).
_ALLOWED_DOC_EXTS = {".pdf", ".docx", ".html", ".htm", ".md", ".txt"}
# Max chars of extracted text handed to the LLM (cost-leak guard, audit H10).
_MAX_DOC_CHARS = 200_000
_ALLOWED_PRODUCT_LINES = {
    "global", "term", "ulip", "rider", "group", "savings_endowment",
    "pension_annuity", "par", "non_par",
}


def _validated_product_line(value: Optional[str]) -> str:
    """Return a canonical, explicit scope or reject the write.

    NULL used to mean global at retrieval time, which made every omitted tag
    silently cross-product. Legacy rows may remain nullable until curated, but
    every new/activated version must state its applicability.
    """
    normalized = (value or "").strip().lower()
    if normalized not in _ALLOWED_PRODUCT_LINES:
        raise HTTPException(
            status_code=400,
            detail="product_line must be an explicit supported scope or global",
        )
    return normalized


def safe_extension(filename: str) -> str:
    """Return a safe, whitelisted file extension (with leading dot) derived
    from `filename`, or '' if unknown/absent. Path components are stripped."""
    ext = os.path.splitext(os.path.basename(filename or ""))[1].lower()
    return ext if ext in _ALLOWED_DOC_EXTS else ""

router = APIRouter(prefix="/rules", tags=["Rules Management"])


async def _safe_rag_upsert(rule_id, db: Session) -> None:
    """Best-effort RAG upsert. Logs and swallows failures — the DB write is
    already committed and a backfill catches missed indexings later."""
    try:
        await rag_upsert_rule(rule_id, db)
    except Exception as e:
        logger.warning(f"RAG upsert failed for rule {rule_id} (non-fatal): {e}")


async def _safe_rag_delete(rule_id) -> None:
    try:
        await rag_delete_rule(rule_id)
    except Exception as e:
        logger.warning(f"RAG delete failed for rule {rule_id} (non-fatal): {e}")


def _actor_id(user) -> Optional[uuid.UUID]:
    return user.get("id") if isinstance(user, dict) else getattr(user, "id", None)


async def _link_source_evidence_or_fail(metadata: dict, rule_id) -> None:
    """Publish only the exact evidence row mapped to a generated rule.

    Activation is refused when the mapping is incomplete or cannot be verified.
    The source indexer additionally checks passage ID, document ID, and exact
    quote text in one UPDATE, so document-wide publication is impossible.
    """
    document_id = metadata.get("source_doc_id")
    passage_id = metadata.get("source_evidence_passage_id")
    source_quote = metadata.get("source_quote")
    has_quote = isinstance(source_quote, str) and bool(source_quote.strip())
    if not document_id or not passage_id or not has_quote:
        raise HTTPException(
            status_code=409,
            detail="Generated rule has no verified source evidence mapping",
        )
    try:
        from app.services.rag.indexers.source_docs_indexer import (
            link_rule_to_source_quote,
        )
        await link_rule_to_source_quote(
            passage_id=passage_id,
            document_id=document_id,
            source_quote=source_quote,
            rule_id=rule_id,
        )
    except Exception as exc:
        logger.error(
            "Source-evidence approval link failed for rule %s: %s",
            rule_id,
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail="Could not verify source evidence; rule remains inactive",
        ) from exc


@router.post("", response_model=dict)
async def create_rule(
    rule: RuleCreate,
    user: dict = Depends(require("rules:write")),
    db: Session = Depends(get_db)
):
    """Create a new compliance rule manually."""
    product_line = _validated_product_line(rule.product_line)
    new_rule = rule_generator_service.create_rule(
        db=db,
        category=rule.category,
        rule_text=rule.rule_text,
        severity=rule.severity,
        keywords=rule.keywords,
        points_deduction=rule.points_deduction,
        created_by=_actor_id(user),
        product_line=product_line,
        jurisdiction=rule.jurisdiction,
    )
    await _safe_rag_upsert(new_rule.id, db)
    return {
        "id": str(new_rule.id),
        "category": new_rule.category,
        "rule_text": new_rule.rule_text,
        "severity": new_rule.severity,
        "is_active": new_rule.is_active,
        "product_line": new_rule.product_line,
    }


@router.get("")
async def list_rules(
    category: Optional[str] = None,
    is_active: Optional[bool] = True,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
    user: dict = Depends(require("rules:read")),
    db: Session = Depends(get_db)
):
    """List all compliance rules with optional filtering."""
    query = db.query(Rule)
    if category:
        query = query.filter(Rule.category == category)
    if is_active is not None:
        query = query.filter(Rule.is_active == is_active)

    total = query.count()
    rules = query.offset(skip).limit(limit).all()

    return {
        "total": total,
        "rules": [
            {
                "id": str(r.id),
                "category": r.category,
                "rule_text": r.rule_text,
                "severity": r.severity,
                "is_active": r.is_active,
                "product_line": r.product_line,
                "points_deduction": float(r.points_deduction) if r.points_deduction else -5.0,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in rules
        ]
    }


@router.get("/{rule_id}")
async def get_rule(rule_id: str, user: dict = Depends(require("rules:read")), db: Session = Depends(get_db)):
    """Get a specific rule by ID."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    return {
        "id": str(rule.id),
        "category": rule.category,
        "rule_text": rule.rule_text,
        "severity": rule.severity,
        "keywords": rule.keywords,
        "is_active": rule.is_active,
        "product_line": rule.product_line,
        "points_deduction": float(rule.points_deduction) if rule.points_deduction else -5.0,
        "is_auto_generated": rule.is_auto_generated,
        "created_at": rule.created_at.isoformat() if rule.created_at else None
    }


@router.patch("/{rule_id}")
async def update_rule(
    rule_id: str,
    is_active: Optional[bool] = None,
    severity: Optional[str] = None,
    rule_text: Optional[str] = None,
    product_line: Optional[str] = None,
    user: dict = Depends(require("rules:write")),
    db: Session = Depends(get_db)
):
    """Update a rule.

    A CONTENT change (severity or rule_text) does NOT mutate the existing row —
    it creates a NEW version (version+1), supersedes and deactivates the old one,
    and links old.superseded_by → new.id. Past violations that snapshotted the
    old rule_version therefore stay traceable to the exact text in force at
    decision time (architect-audit: rules must be versioned, not mutated).

    A pure activate/deactivate (is_active only) is lifecycle, not a content
    rewrite, so it stays in place.
    """
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if rule.superseded_by is not None:
        raise HTTPException(
            status_code=409,
            detail="This rule version is superseded; edit the current leaf version",
        )

    if product_line is not None:
        product_line = _validated_product_line(product_line)
    will_be_active = rule.is_active if is_active is None else is_active
    if will_be_active:
        # Every active version needs an explicit applicability decision. This
        # also covers content edits, which create an active successor version.
        effective_scope = product_line if product_line is not None else rule.product_line
        product_line = _validated_product_line(effective_scope)

    is_content_change = (
        (severity is not None and severity != rule.severity)
        or (rule_text is not None and rule_text != rule.rule_text)
        or (product_line is not None and product_line != rule.product_line)
    )

    # Generated rules are created as inactive drafts so the documented review
    # step is a real approval gate. Because a draft has never participated in a
    # grade, its first edit can safely happen in place; this preserves the
    # source-document linkage. Once activated, normal immutable versioning
    # applies to every later content/scope change.
    metadata = dict(rule.rule_metadata or {})
    is_unpublished_draft = (
        not rule.is_active
        and rule.is_auto_generated
        and metadata.get("lifecycle") == "draft_pending_review"
        and rule.superseded_by is None
    )
    if is_unpublished_draft:
        if is_active is True:
            # Publish the exact evidence row before changing lifecycle state.
            # If verification fails, the draft remains untouched and inactive.
            await _link_source_evidence_or_fail(metadata, rule.id)
        if severity is not None:
            rule.severity = severity
        if rule_text is not None:
            rule.rule_text = rule_text
        if product_line is not None:
            rule.product_line = product_line
        if is_active is not None:
            rule.is_active = is_active
        if rule.is_active:
            metadata["lifecycle"] = "reviewed_active"
            metadata["approved_at"] = datetime.now(timezone.utc).isoformat()
            actor_id = _actor_id(user)
            if actor_id:
                metadata["approved_by"] = str(actor_id)
            if rule.effective_date is None:
                rule.effective_date = datetime.now(timezone.utc)
        rule.rule_metadata = metadata
        db.commit()
        db.refresh(rule)
        await _safe_rag_upsert(rule.id, db)
        return {
            "id": str(rule.id), "category": rule.category,
            "rule_text": rule.rule_text, "severity": rule.severity,
            "is_active": rule.is_active, "product_line": rule.product_line,
            "version": rule.version,
        }

    if not is_content_change:
        # Lifecycle-only (activate/deactivate) — mutate in place.
        if is_active is not None:
            was_active = bool(rule.is_active)
            if is_active and not was_active and rule.is_auto_generated:
                await _link_source_evidence_or_fail(
                    dict(rule.rule_metadata or {}), rule.id
                )
            rule.is_active = is_active
            if is_active and not was_active:
                rule.effective_date = datetime.now(timezone.utc)
                lifecycle_metadata = dict(rule.rule_metadata or {})
                lifecycle_metadata["lifecycle"] = "reviewed_active"
                lifecycle_metadata["approved_at"] = datetime.now(timezone.utc).isoformat()
                actor_id = _actor_id(user)
                if actor_id:
                    lifecycle_metadata["approved_by"] = str(actor_id)
                rule.rule_metadata = lifecycle_metadata
        db.commit()
        db.refresh(rule)
        await _safe_rag_upsert(rule.id, db)
        return {
            "id": str(rule.id), "category": rule.category, "rule_text": rule.rule_text,
            "severity": rule.severity, "is_active": rule.is_active,
            "product_line": rule.product_line, "version": rule.version,
        }

    # Content change → create a new version row.
    new_metadata = dict(rule.rule_metadata or {})
    if will_be_active:
        new_metadata["lifecycle"] = "reviewed_active"
        new_metadata["approved_at"] = datetime.now(timezone.utc).isoformat()
        actor_id = _actor_id(user)
        if actor_id:
            new_metadata["approved_by"] = str(actor_id)

    new_rule = Rule(
        category=rule.category,
        rule_text=rule_text if rule_text is not None else rule.rule_text,
        severity=severity if severity is not None else rule.severity,
        keywords=rule.keywords,
        pattern=rule.pattern,
        is_active=will_be_active,
        rule_metadata=new_metadata,
        points_deduction=rule.points_deduction,
        created_by=rule.created_by,
        is_auto_generated=rule.is_auto_generated,
        generated_from_industry=rule.generated_from_industry,
        generation_source=rule.generation_source,
        confidence_score=rule.confidence_score,
        version=(rule.version or 1) + 1,
        effective_date=func.now(),
        product_line=product_line if product_line is not None else rule.product_line,
        jurisdiction=rule.jurisdiction,
        # Learned trust (Beta-Binomial reliability) carries forward to the new
        # version — an edit isn't a fresh rule, so it shouldn't reset to
        # "no feedback history" and lose everything reviewers already taught it.
        reliability_alpha=rule.reliability_alpha,
        reliability_beta=rule.reliability_beta,
    )
    db.add(new_rule)
    db.flush()  # get new_rule.id
    if new_rule.is_active and new_rule.is_auto_generated:
        try:
            await _link_source_evidence_or_fail(new_metadata, new_rule.id)
        except HTTPException:
            db.rollback()
            raise

    # Supersede + retire the old version.
    rule.is_active = False
    rule.superseded_by = new_rule.id
    db.commit()
    db.refresh(new_rule)

    # Old row is now inactive; its RAG index entry is stale and must be
    # cleaned up so retrieval doesn't keep matching a superseded rule version
    # (mirrors the cleanup DELETE /rules/{id} already performs).
    await _safe_rag_delete(rule.id)

    # New version goes into RAG; old row stays (is_active=false → filtered out).
    await _safe_rag_upsert(new_rule.id, db)

    return {
        "id": str(new_rule.id),
        "category": new_rule.category,
        "rule_text": new_rule.rule_text,
        "severity": new_rule.severity,
        "is_active": new_rule.is_active,
        "product_line": new_rule.product_line,
        "version": new_rule.version,
        "superseded_rule_id": str(rule.id),
    }


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str, user: dict = Depends(require("rules:write")), db: Session = Depends(get_db)):
    """Discard an unpublished draft or retire a published rule.

    Published/versioned rows remain for violation provenance; only a draft
    that never participated in grading is safe to remove physically.
    """
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    metadata = dict(rule.rule_metadata or {})
    is_unpublished_draft = (
        not rule.is_active
        and rule.is_auto_generated
        and metadata.get("lifecycle") == "draft_pending_review"
        and rule.superseded_by is None
    )
    if is_unpublished_draft:
        db.delete(rule)
        action = "deleted"
    else:
        rule.is_active = False
        metadata["lifecycle"] = "retired"
        metadata["retired_at"] = datetime.now(timezone.utc).isoformat()
        rule.rule_metadata = metadata
        db.add(rule)
        action = "retired"
    db.commit()

    await _safe_rag_delete(rule_id)

    return {"message": f"Rule {action}", "id": rule_id, "action": action}


@router.post("/generate-from-document", dependencies=[Depends(llm_rate_limit)])
async def generate_rules_from_document(
    title: str = Form(...),
    product_line: str = Form(...),
    instructions: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    content: Optional[str] = Form(default=None),
    user: dict = Depends(require("rules:generate")),
    db: Session = Depends(get_db)
):
    """
    Generate compliance rules from a document using AI.
    Accepts either file upload or raw text content.
    """
    from app.config import settings

    product_line = _validated_product_line(product_line)

    # Get content
    document_content = content or ""
    if file and file.filename:
        ext = safe_extension(file.filename)
        if not ext:
            raise HTTPException(
                status_code=400,
                detail="Unsupported file type. Allowed: pdf, docx, html, md, txt",
            )
        os.makedirs(settings.upload_dir, exist_ok=True)
        # Safe write path: {uuid}{ext} — never embeds the client filename.
        file_path = os.path.join(settings.upload_dir, f"{uuid.uuid4()}{ext}")
        size = 0
        with open(file_path, "wb") as f:
            while chunk := await file.read(8192):
                size += len(chunk)
                if size > settings.max_upload_size:
                    os.remove(file_path)
                    raise HTTPException(status_code=413, detail="File too large")
                f.write(chunk)

        # Extract text from file
        from app.services.preprocessing_service import ContextEngineeringService
        from app.database import SessionLocal
        temp_db = SessionLocal()
        try:
            service = ContextEngineeringService(temp_db)
            content_type = {
                ".pdf": "pdf", ".docx": "docx", ".html": "html",
                ".htm": "html", ".md": "markdown", ".txt": "text",
            }[ext]
            document_content = await service._extract_from_file(file_path, content_type)
        finally:
            temp_db.close()
            os.remove(file_path)

    if not document_content:
        raise HTTPException(status_code=400, detail="No content provided for rule generation")

    # Bound the text handed to the LLM (cost-leak guard, audit H10).
    if len(document_content) > _MAX_DOC_CHARS:
        logger.warning(
            "Rule-gen document truncated from %d to %d chars",
            len(document_content), _MAX_DOC_CHARS,
        )
        document_content = document_content[:_MAX_DOC_CHARS]

    result = await rule_generator_service.generate_rules_from_text(
        document_content=document_content,
        document_title=title,
        created_by_user_id=_actor_id(user),
        db=db,
        instructions=instructions,
        product_line=product_line,
    )

    return result
