from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime


class RuleCreate(BaseModel):
 category: str
 rule_text: str
 severity: str = "medium"
 keywords: Optional[List[str]] = None
 points_deduction: float = -5.0


class RuleResponse(BaseModel):
 id: str
 category: str
 rule_text: str
 severity: str
 is_active: bool
 created_at: datetime

 class Config:
 from_attributes = True
