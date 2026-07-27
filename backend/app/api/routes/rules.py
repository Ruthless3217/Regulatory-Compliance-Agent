"""
Rules Management API Routes

Handles CRUD for compliance rules and AI-based rule generation from documents.
"""
import logging
import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from typing import Optional, List

from app.api.rate_limit import llm_rate_limit
from app.database import get_db
from app.models.rule import Rule
from app.models.user import User
from app.services.rule_generator_service import rule_generator_service
from app.services.rag.indexers.rules_indexer import (
    delete_rule as rag_delete_rule,
    upsert_rule as rag_upsert_rule,
)
from app.schemas.rule import RuleCreate, RuleResponse
from app.auth.dependencies import require
from app.services.observability import audit


def _serialize_rule(rule: Rule) -> dict:
    """Compact rule snapshot for before/after audit records."""
    return {
        "id": str(rule.id),
        "category": rule.category,
        "rule_text": rule.rule_text,
        "severity": rule.severity,
        "is_active": rule.is_active,
        "version": getattr(rule, "version", None),
        "points_deduction": float(rule.points_deduction) if rule.points_deduction else None,
    }

logger = logging.getLogger(__name__)

# Document types we accept for rule generation. The stored filename is built
# as {uuid}{safe_ext}, so a malicious file.filename (traversal, null bytes)
# cannot influence the write path (audit H9).
_ALLOWED_DOC_EXTS = {".pdf", ".docx", ".html", ".htm", ".md", ".txt"}
# Max chars of extracted text handed to the LLM (cost-leak guard, audit H10).
_MAX_DOC_CHARS = 200_000


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
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("rules:write")),
):
    """Create a new compliance rule manually."""
    new_rule = rule_generator_service.create_rule(
        db=db,
        category=rule.category,
        rule_text=rule.rule_text,
        severity=rule.severity,
        keywords=rule.keywords,
        points_deduction=rule.points_deduction
    )
    await _safe_rag_upsert(new_rule.id, db)
    await audit.record("rule_created", actor=user, request=request,
                       target_type="rule", target_id=str(new_rule.id),
                       after=_serialize_rule(new_rule))
    return {
        "id": str(new_rule.id),
        "category": new_rule.category,
        "rule_text": new_rule.rule_text,
        "severity": new_rule.severity,
        "is_active": new_rule.is_active
    }


@router.get("")
async def list_rules(
    category: Optional[str] = None,
    is_active: Optional[bool] = True,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
    _perm=Depends(require("rules:read")),
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
                "points_deduction": float(r.points_deduction) if r.points_deduction else -5.0,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in rules
        ]
    }


@router.get("/{rule_id}")
async def get_rule(
    rule_id: str,
    db: Session = Depends(get_db),
    _perm=Depends(require("rules:read")),
):
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
        "points_deduction": float(rule.points_deduction) if rule.points_deduction else -5.0,
        "is_auto_generated": rule.is_auto_generated,
        "created_at": rule.created_at.isoformat() if rule.created_at else None
    }


@router.patch("/{rule_id}")
async def update_rule(
    rule_id: str,
    request: Request,
    is_active: Optional[bool] = None,
    severity: Optional[str] = None,
    rule_text: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require("rules:write")),
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

    before = _serialize_rule(rule)  # snapshot for the audit record

    is_content_change = (
        (severity is not None and severity != rule.severity)
        or (rule_text is not None and rule_text != rule.rule_text)
    )

    if not is_content_change:
        # Lifecycle-only (activate/deactivate) — mutate in place.
        if is_active is not None:
            rule.is_active = is_active
        db.commit()
        db.refresh(rule)
        await _safe_rag_upsert(rule.id, db)
        await audit.record(
            "rule_activated" if rule.is_active else "rule_deactivated",
            actor=user, request=request, target_type="rule", target_id=str(rule.id),
            before=before, after=_serialize_rule(rule),
        )
        return {
            "id": str(rule.id), "category": rule.category, "rule_text": rule.rule_text,
            "severity": rule.severity, "is_active": rule.is_active, "version": rule.version,
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
        product_line=rule.product_line,
        jurisdiction=rule.jurisdiction,
    )
    db.add(new_rule)
    db.flush()  # get new_rule.id

    # Supersede + retire the old version.
    rule.is_active = False
    rule.superseded_by = new_rule.id
    db.commit()
    db.refresh(new_rule)

    # New version goes into RAG; old row stays (is_active=false → filtered out).
    await _safe_rag_upsert(new_rule.id, db)

    await audit.record(
        "rule_updated", actor=user, request=request, target_type="rule",
        target_id=str(new_rule.id), before=before, after=_serialize_rule(new_rule),
        metadata={"version": new_rule.version, "superseded": str(rule.id)},
    )

    return {
        "id": str(new_rule.id),
        "category": new_rule.category,
        "rule_text": new_rule.rule_text,
        "severity": new_rule.severity,
        "is_active": new_rule.is_active,
        "version": new_rule.version,
        "superseded_rule_id": str(rule.id),
    }


@router.delete("/{rule_id}")
async def delete_rule(
    rule_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("rules:write")),
):
    """Delete a rule."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    before = _serialize_rule(rule)
    db.delete(rule)
    db.commit()

    await _safe_rag_delete(rule_id)
    await audit.record("rule_deleted", actor=user, request=request,
                       target_type="rule", target_id=str(rule_id), before=before)

    return {"message": "Rule deleted", "id": rule_id}


@router.post("/generate-from-document", dependencies=[Depends(llm_rate_limit)])
async def generate_rules_from_document(
    request: Request,
    title: str = Form(...),
    instructions: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    content: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require("rules:generate")),
):
    """
    Generate compliance rules from a document using AI.
    Accepts either file upload or raw text content.
    """
    from app.config import settings

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

    # Attribute generated rules to the authenticated actor (created_by FK).
    result = await rule_generator_service.generate_rules_from_text(
        document_content=document_content,
        document_title=title,
        created_by_user_id=str(user.id),
        db=db,
        instructions=instructions
    )

    await audit.record("rules_generated", actor=user, request=request,
                       target_type="rule",
                       metadata={"title": title, "result_summary": str(result)[:500]})

    return result
