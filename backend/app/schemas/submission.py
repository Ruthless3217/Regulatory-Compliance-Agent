from typing import Optional
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
