"""
Rules Management API Routes

Handles CRUD for compliance rules and AI-based rule generation from documents.
"""
import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import Optional, List

from app.database import get_db
from app.models.rule import Rule
from app.services.rule_generator_service import rule_generator_service
from app.services.rag.indexers.rules_indexer import (
    delete_rule as rag_delete_rule,
    upsert_rule as rag_upsert_rule,
)
from app.schemas.rule import RuleCreate, RuleResponse

logger = logging.getLogger(__name__)

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
    db: Session = Depends(get_db)
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
    skip: int = 0,
    limit: int = 50,
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
                "points_deduction": float(r.points_deduction) if r.points_deduction else -5.0,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in rules
        ]
    }


@router.get("/{rule_id}")
async def get_rule(rule_id: str, db: Session = Depends(get_db)):
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
    is_active: Optional[bool] = None,
    severity: Optional[str] = None,
    rule_text: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Update a rule (activate/deactivate, update severity or text)."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if is_active is not None:
        rule.is_active = is_active
    if severity is not None:
        rule.severity = severity
    if rule_text is not None:
        rule.rule_text = rule_text

    db.commit()
    db.refresh(rule)

    # Keep RAG in sync. If the rule was deactivated, the embedding stays
    # (filtered out at query time) — same row, just is_active=false.
    await _safe_rag_upsert(rule.id, db)

    return {
        "id": str(rule.id),
        "category": rule.category,
        "rule_text": rule.rule_text,
        "severity": rule.severity,
        "is_active": rule.is_active
    }


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str, db: Session = Depends(get_db)):
    """Delete a rule."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    db.delete(rule)
    db.commit()

    await _safe_rag_delete(rule_id)

    return {"message": "Rule deleted", "id": rule_id}


@router.post("/generate-from-document")
async def generate_rules_from_document(
    title: str = Form(...),
    instructions: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    content: Optional[str] = Form(default=None),
    db: Session = Depends(get_db)
):
    """
    Generate compliance rules from a document using AI.
    Accepts either file upload or raw text content.
    """
    import os
    from app.config import settings

    # Get content
    document_content = content or ""
    if file and file.filename:
        os.makedirs(settings.upload_dir, exist_ok=True)
        file_path = os.path.join(settings.upload_dir, f"{uuid.uuid4()}_{file.filename}")
        with open(file_path, "wb") as f:
            file_bytes = await file.read()
            f.write(file_bytes)

        # Extract text from file
        from app.services.preprocessing_service import ContextEngineeringService
        from app.database import SessionLocal
        temp_db = SessionLocal()
        try:
            service = ContextEngineeringService(temp_db)
            fname = file.filename.lower()
            if fname.endswith(".pdf"):
                content_type = "pdf"
            elif fname.endswith(".docx"):
                content_type = "docx"
            elif fname.endswith((".html", ".htm")):
                content_type = "html"
            elif fname.endswith(".md"):
                content_type = "markdown"
            else:
                content_type = "text"
            document_content = await service._extract_from_file(file_path, content_type)
        finally:
            temp_db.close()

    if not document_content:
        raise HTTPException(status_code=400, detail="No content provided for rule generation")

    # v1 has no auth; created_by is nullable on the rules table. Passing None
    # avoids the FK to a non-existent system-user row.
    result = await rule_generator_service.generate_rules_from_text(
        document_content=document_content,
        document_title=title,
        created_by_user_id=None,
        db=db,
        instructions=instructions
    )

    return result
