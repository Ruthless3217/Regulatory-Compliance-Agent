from typing import List, Literal, Optional
from pydantic import BaseModel
from datetime import datetime
import uuid


class SubmissionCreate(BaseModel):
    title: str
    content_type: str
    original_content: Optional[str] = None


class SubmissionResponse(BaseModel):
    id: str
    title: str
    content_type: str
    status: str
    approval_status: str
    submitted_at: datetime

    class Config:
        from_attributes = True


# --- Content revisions (migration 0026) ------------------------------------
# The one mutation primitive behind manual edits, apply-fix, bulk-apply-fixes,
# and restore — restore is just a re-POST of an old revision's content with
# source='restore', there is no separate restore endpoint.

RevisionSource = Literal["manual_edit", "apply_fix", "bulk_apply_fixes", "restore"]


class SubmissionRevisionCreate(BaseModel):
    content: str
    source: RevisionSource
    note: Optional[str] = None
    applied_violation_ids: Optional[List[uuid.UUID]] = None


class SubmissionRevisionResponse(BaseModel):
    id: str
    submission_id: str
    revision_number: int
    content: str
    source: str
    note: Optional[str] = None
    applied_violation_ids: List[str] = []
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# --- Document comments (migration 0027) -------------------------------------
# Mirrors comparisons.py's ComparisonAnnotation shape, but each comment is its
# own row (no per-change-id upsert — a submission's document has no
# comparison-style stable change id to key off).

class DocumentCommentCreate(BaseModel):
    anchor_text: Optional[str] = None
    page_number: Optional[int] = None
    body: str


class DocumentCommentUpdate(BaseModel):
    body: Optional[str] = None
    resolved: Optional[bool] = None


class DocumentCommentResponse(BaseModel):
    id: str
    submission_id: str
    anchor_text: Optional[str] = None
    page_number: Optional[int] = None
    body: str
    resolved: bool
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
