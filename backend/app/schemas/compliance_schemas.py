from typing import List, Optional
from pydantic import BaseModel, Field


class ViolationSchema(BaseModel):
    id: Optional[str] = Field(None, description="Persisted violation UUID (set after persist)")
    category: str = Field(..., description="Category of the violation: one of terminology issue, legal language, missing reference, disclaimer issue, other")
    severity: str = Field(..., description="Severity level: one of critical, moderate, informational")
    rule_id: Optional[str] = Field(None, description="The ID of the violated rule — MUST be one of the rule_id values shown in the input rules block")
    description: str = Field(..., description="Brief description of the violation")
    location: Optional[str] = Field(None, description="Location reference in the text")
    current_text: Optional[str] = Field(..., description="The exact problematic phrase from the submission — copy verbatim, no paraphrase")
    suggested_fix: Optional[str] = Field(None, description="Suggested compliant rewrite of current_text")
    auto_fixable: bool = Field(False, description="Whether this can be auto-fixed by a simple string substitution")
    chunk_index: Optional[int] = Field(None, description="Index of the content chunk")
    # P1.3 — Confidence + citation are required for audit-defensible findings
    confidence: float = Field(
        0.85,
        ge=0.0,
        le=1.0,
        description="0.0–1.0 confidence that this is a real violation of the cited rule. Use <0.6 if uncertain."
    )
    regulator_quote: Optional[str] = Field(
        None,
        description="Verbatim quote from the rule's regulator passage (≤200 chars). Leave null only if no source passage exists in the input."
    )


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
