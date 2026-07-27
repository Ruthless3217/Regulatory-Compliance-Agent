from typing import Optional
from pydantic import BaseModel
from datetime import datetime
import uuid


class SubmissionCreate(BaseModel):
    title: str
    content_type: str
    original_content: Optional[str] = None
    # Semantic document type (Workstream A) — product_marketing | blog_article |
    # social | email | website | other. Gates product mandatory elements (UIN).
    document_type: Optional[str] = None


class SubmissionResponse(BaseModel):
    id: str
    title: str
    content_type: str
    document_type: Optional[str] = None
    status: str
    approval_status: str
    submitted_at: datetime

    class Config:
        from_attributes = True
