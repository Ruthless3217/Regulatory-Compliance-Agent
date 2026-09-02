from typing import List, Literal, Optional
from pydantic import BaseModel
from datetime import datetime
import uuid


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
    # Lexical editor state for this revision, and the HTML the client
    # serialized from it at the same instant. Optional: a text-only edit path
    # (and every pre-existing client) still posts just `content`.
    lexical_state: Optional[dict] = None
    lexical_html: Optional[str] = None


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
