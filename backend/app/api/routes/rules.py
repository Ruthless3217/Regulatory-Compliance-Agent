"""
Rules Management API Routes

Handles CRUD for compliance rules and AI-based rule generation from documents.
"""
import logging
import os
import uuid
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


@router.post("", response_model=dict)
async def create_rule(
    rule: RuleCreate,
    user: dict = Depends(require("rules:write")),
    db: Session = Depends(get_db)
):
    """Create a new compliance rule manually."""
    new_rule = rule_generator_service.create_rule(
        db=db,
        category=rule.category,
        rule_text=rule.rule_text,
        severity=rule.severity,
        keywords=rule.keywords,
        points_deduction=rule.points_deduction,
        product_line=rule.product_line,
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
            rule.is_active = is_active
        db.commit()
        db.refresh(rule)
        await _safe_rag_upsert(rule.id, db)
        return {
            "id": str(rule.id), "category": rule.category, "rule_text": rule.rule_text,
            "severity": rule.severity, "is_active": rule.is_active,
            "product_line": rule.product_line, "version": rule.version,
        }

    # Content change → create a new version row.
    new_rule = Rule(
        category=rule.category,
        rule_text=rule_text if rule_text is not None else rule.rule_text,
        severity=severity if severity is not None else rule.severity,
        keywords=rule.keywords,
        pattern=rule.pattern,
        is_active=is_active if is_active is not None else True,
        rule_metadata=rule.rule_metadata,
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
    """Delete a rule."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    db.delete(rule)
    db.commit()

    await _safe_rag_delete(rule_id)

    return {"message": "Rule deleted", "id": rule_id}


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

    product_line = product_line.strip().lower()
    if product_line not in _ALLOWED_PRODUCT_LINES:
        raise HTTPException(
            status_code=400,
            detail="product_line must be an explicit supported scope or global",
        )

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

    if not document_content:
        raise HTTPException(status_code=400, detail="No content provided for rule generation")

    # Bound the text handed to the LLM (cost-leak guard, audit H10).
    if len(document_content) > _MAX_DOC_CHARS:
        logger.warning(
            "Rule-gen document truncated from %d to %d chars",
            len(document_content), _MAX_DOC_CHARS,
        )
        document_content = document_content[:_MAX_DOC_CHARS]

    # v1 has no auth; created_by is nullable on the rules table. Passing None
    # avoids the FK to a non-existent system-user row.
    result = await rule_generator_service.generate_rules_from_text(
        document_content=document_content,
        document_title=title,
        created_by_user_id=None,
        db=db,
        instructions=instructions,
        product_line=product_line,
    )

    return result
