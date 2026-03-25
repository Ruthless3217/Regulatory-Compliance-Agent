"""
Rule Generator Service: Manages compliance rules.
Handles CRUD operations and active rule retrieval for the compliance engine.
"""
import logging
import uuid
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.rule import Rule
from app.services.llm_service import llm_service
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RuleExtractionResult(BaseModel):
    """Schema for LLM-extracted rules from documents."""
    rules: List[Dict[str, Any]] = Field(default_factory=list)
    document_summary: str = Field(default="")
    extraction_notes: str = Field(default="")


class RuleGeneratorService:
    """
    Service for managing and generating compliance rules.
    Provides rule retrieval and dynamic rule generation from documents.
    """

    def __init__(self):
        self.llm = llm_service

    def get_active_rules(
        self,
        db: Session,
        project_id: Optional[uuid.UUID] = None
    ) -> Dict[str, List[Rule]]:
        """
        Retrieve all active rules, grouped by category.
        If project_id is provided, returns rules for that project + global rules.
        """
        try:
            query = db.query(Rule).filter(Rule.is_active == True)

            if project_id:
                query = query.filter(
                    (Rule.project_id == project_id) | (Rule.project_id == None)
                )

            rules = query.all()

            # Group by category
            grouped: Dict[str, List[Rule]] = {}
            for rule in rules:
                category = rule.category
                if category not in grouped:
                    grouped[category] = []
                grouped[category].append(rule)

            logger.info(f"Loaded {len(rules)} active rules across {len(grouped)} categories")
            return grouped

        except Exception as e:
            logger.error(f"Failed to load active rules: {e}")
            return {}

    def get_rules_by_category(
        self,
        db: Session,
        category: str,
        project_id: Optional[uuid.UUID] = None
    ) -> List[Rule]:
        """Get active rules for a specific category."""
        try:
            query = db.query(Rule).filter(
                Rule.is_active == True,
                Rule.category == category
            )
            if project_id:
                query = query.filter(
                    (Rule.project_id == project_id) | (Rule.project_id == None)
                )
            return query.all()
        except Exception as e:
            logger.error(f"Failed to load rules for category {category}: {e}")
            return []

    def create_rule(
        self,
        db: Session,
        category: str,
        rule_text: str,
        severity: str = "medium",
        keywords: Optional[List[str]] = None,
        points_deduction: float = -5.0,
        created_by: Optional[uuid.UUID] = None,
        project_id: Optional[uuid.UUID] = None,
        metadata: Optional[Dict] = None
    ) -> Rule:
        """Create a new compliance rule."""
        rule = Rule(
            category=category,
            rule_text=rule_text,
            severity=severity,
            keywords=keywords or [],
            points_deduction=points_deduction,
            created_by=created_by,
            project_id=project_id,
            rule_metadata=metadata,
            is_active=True
        )
        db.add(rule)
        db.commit()
        db.refresh(rule)
        logger.info(f"Created rule {rule.id} in category '{category}'")
        return rule

    async def generate_rules_from_text(
        self,
        document_content: str,
        document_title: str,
        created_by_user_id: uuid.UUID,
        db: Session,
        project_id: Optional[uuid.UUID] = None,
        instructions: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate compliance rules from document text using LLM.
        """
        logger.info(f"Starting rule generation from document: {document_title}")

        result = {
            "success": False,
            "rules_created": 0,
            "rules_failed": 0,
            "rules": [],
            "errors": []
        }

        if not document_content or len(document_content) < 50:
            result["errors"].append("Document content too short or empty")
            return result

        # Build extraction prompt
        extra_instructions = f"\n\nSpecial Instructions: {instructions}" if instructions else ""
        prompt = f"""Analyze the following regulatory compliance document and extract all compliance rules.

Document Title: {document_title}

Document Content:
{document_content[:8000]}  

{extra_instructions}

Extract all compliance rules from this document. For each rule, determine:
1. rule_text: The exact rule or requirement
2. category: One of [regulatory, brand, seo, legal, financial, safety]
3. severity: One of [critical, high, medium, low]
4. keywords: Key terms associated with this rule

Return a list of rules in JSON format."""

        system_prompt = """You are an expert regulatory compliance analyst. 
Extract specific, actionable compliance rules from documents.
Return ONLY valid JSON."""

        try:
            extraction_result = await self.llm.generate_structured_response(
                prompt=prompt,
                output_model=RuleExtractionResult,
                system_prompt=system_prompt
            )

            # Save extracted rules to DB
            for rule_data in extraction_result.rules:
                try:
                    rule = self.create_rule(
                        db=db,
                        category=rule_data.get("category", "regulatory"),
                        rule_text=rule_data.get("rule_text", ""),
                        severity=rule_data.get("severity", "medium"),
                        keywords=rule_data.get("keywords", []),
                        created_by=created_by_user_id,
                        project_id=project_id,
                        metadata={"source": document_title}
                    )
                    result["rules"].append({
                        "id": str(rule.id),
                        "category": rule.category,
                        "rule_text": rule.rule_text,
                        "severity": rule.severity
                    })
                    result["rules_created"] += 1
                except Exception as e:
                    logger.error(f"Failed to save rule: {e}")
                    result["rules_failed"] += 1
                    result["errors"].append(str(e))

            result["success"] = result["rules_created"] > 0
            logger.info(f"Generated {result['rules_created']} rules from document")
            return result

        except Exception as e:
            logger.error(f"Rule generation failed: {e}")
            result["errors"].append(f"LLM rule extraction failed: {str(e)}")
            return result


# Singleton instance
rule_generator_service = RuleGeneratorService()
