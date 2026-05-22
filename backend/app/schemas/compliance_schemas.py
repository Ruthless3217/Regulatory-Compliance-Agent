from typing import List, Optional
from pydantic import BaseModel, Field


class ViolationSchema(BaseModel):
    id: Optional[str] = Field(None, description="Persisted violation UUID (set after persist)")
    category: str = Field(..., description="Category of the violation: irdai, brand, sebi (canonical) or regulatory/seo (legacy)")
    severity: str = Field(..., description="Severity level: critical, high, medium, low")
    rule_id: Optional[str] = Field(None, description="The ID of the violated rule")
    description: str = Field(..., description="Brief description of the violation")
    location: Optional[str] = Field(None, description="Location reference in the text")
    current_text: Optional[str] = Field(None, description="The problematic text")
    suggested_fix: Optional[str] = Field(None, description="Suggested correction")
    auto_fixable: bool = Field(False, description="Whether this can be auto-fixed")
    chunk_index: Optional[int] = Field(None, description="Index of the content chunk")


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
