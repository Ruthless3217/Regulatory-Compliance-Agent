from typing import List, Optional
from pydantic import BaseModel, Field


class ViolationSchema(BaseModel):
    category: str = Field(..., description="Category of the violation: regulatory, brand, seo")
    severity: str = Field(..., description="Severity level: critical, high, medium, low")
    rule_id: Optional[str] = Field(None, description="The ID of the violated rule")
    description: str = Field(..., description="Brief description of the violation")
    location: Optional[str] = Field(None, description="Location reference in the text")
    current_text: Optional[str] = Field(None, description="The problematic text")
    suggested_fix: Optional[str] = Field(None, description="Suggested correction")
    auto_fixable: bool = Field(False, description="Whether this can be auto-fixed")


class ComplianceAnalysisResult(BaseModel):
    violations: List[ViolationSchema] = Field(
        default_factory=list,
        description="List of detected violations"
    )
    overall_assessment: str = Field(
        ...,
        description="Brief summary of compliance status"
    )
    key_issues: List[str] = Field(
        default_factory=list,
        description="List of key issues identified"
    )
