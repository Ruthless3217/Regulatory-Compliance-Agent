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


class RulesUnavailableError(RuntimeError):
    """Raised when active rules cannot be loaded due to a DB error.

    Must propagate (fail closed): grading a document with zero rules because of
    an infrastructure failure would under-flag it (≈ falsely clean). The graph
    catches this and marks the run degraded → needs_review.
    """


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

        FAIL CLOSED: a DB error here must NOT be swallowed into ``{}``. An empty
        dict is indistinguishable from "zero rules configured", and the analysis
        path would then grade a document with no rule grounding while looking
        healthy (fail open). On any DB error we raise so the caller can mark the
        run degraded → needs_review. A genuinely empty (but successful) query
        still returns ``{}``.
        """
        try:
            query = db.query(Rule).filter(Rule.is_active == True)

            if project_id:
                query = query.filter(
                    (Rule.project_id == project_id) | (Rule.project_id == None)
                )

            rules = query.all()
        except SQLAlchemyError as e:
            logger.error(f"Failed to load active rules (failing closed): {e}")
            raise RulesUnavailableError(
                "Active-rule load failed; refusing to grade without rule grounding"
            ) from e

        # Group by category
        grouped: Dict[str, List[Rule]] = {}
        for rule in rules:
            category = rule.category
            if category not in grouped:
                grouped[category] = []
            grouped[category].append(rule)

        logger.info(f"Loaded {len(rules)} active rules across {len(grouped)} categories")
        return grouped

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
        # project_id is accepted for forward-compat but the Rule model has no
        # such column yet — silently drop it instead of breaking the insert.
        _ = project_id
        is_auto = bool(metadata and metadata.get("source"))
        rule = Rule(
            category=category,
            rule_text=rule_text,
            severity=severity,
            keywords=keywords or [],
            points_deduction=points_deduction,
            created_by=created_by,
            rule_metadata=metadata,
            is_active=True,
            is_auto_generated=is_auto,
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
        created_by_user_id: Optional[uuid.UUID],
        db: Session,
        project_id: Optional[uuid.UUID] = None,
        instructions: Optional[str] = None,
        regulator: str = "irdai",
    ) -> Dict[str, Any]:
        """
        Generate compliance rules from document text using LLM, and index the
        source passages into the RAG store so generated rules can carry a
        verbatim citation back to the regulator passage that produced them.
        """
        logger.info(f"Starting rule generation from document: {document_title}")

        result: Dict[str, Any] = {
            "success": False,
            "rules_created": 0,
            "rules_failed": 0,
            "rules": [],
            "errors": [],
            "source_doc_id": None,
            "source_passages_indexed": 0,
        }

        if not document_content or len(document_content) < 50:
            result["errors"].append("Document content too short or empty")
            return result

        # 1. Index the source document into rag_source_docs (non-fatal on failure).
        document_id = uuid.uuid4()
        result["source_doc_id"] = str(document_id)
        indexed_passages: List = []
        try:
            from app.services.rag.indexers.source_docs_indexer import index_source_document
            indexed_passages = await index_source_document(
                document_id=document_id,
                document_title=document_title,
                regulator=regulator,
                full_text=document_content,
            )
            result["source_passages_indexed"] = len(indexed_passages)
        except Exception as e:
            logger.warning(f"RAG source-doc indexing failed (non-fatal): {e}")

        # 2. LLM extraction.
        extra_instructions = f"\n\nSpecial Instructions: {instructions}" if instructions else ""
        category_hint = (
            f"\n\nDefault category for rules from this document: '{regulator}' "
            f"(override only if a rule is clearly about a different domain)."
        )
        prompt = f"""Extract every compliance rule from this regulatory document.

Document Title: {document_title}
Regulator: {regulator}

If the document has explicit "### Rule:" or "Rule:" headings, treat each as one
rule and capture its full text + intent. If it doesn't, extract each distinct
"must / must not / required / prohibited / mandatory" clause as a rule.

Skip pure background paragraphs, intros, and overviews — only extract rules
that a marketing reviewer can directly check ad copy against.

Document Content:
{document_content[:16000]}
{category_hint}
{extra_instructions}

For each rule output:
1. rule_text — the rule itself, rewritten as a single concise checkable statement
2. category — one of: irdai, sebi, brand, seo, regulatory, legal, financial
3. severity — critical | high | medium | low (default medium)
4. keywords — 3-6 short terms a reviewer would search for

Return at least one rule unless the document genuinely has none.
"""

        system_prompt = (
            "You are an expert insurance/financial compliance analyst for Bajaj "
            " Life's marketing team. Extract specific, actionable rules "
            "from regulator documents. Be exhaustive — every distinct rule the "
            "document contains must be returned. Return ONLY valid JSON."
        )

        try:
            extraction_result = await self.llm.generate_structured_response(
                prompt=prompt,
                output_model=RuleExtractionResult,
                system_prompt=system_prompt
            )

            created_rule_ids: List[uuid.UUID] = []
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
                        metadata={
                            "source": document_title,
                            "source_doc_id": str(document_id),
                        },
                    )
                    created_rule_ids.append(rule.id)
                    result["rules"].append({
                        "id": str(rule.id),
                        "category": rule.category,
                        "rule_text": rule.rule_text,
                        "severity": rule.severity
                    })
                    result["rules_created"] += 1
                    # Best-effort: index this new rule into rag_rules.
                    try:
                        from app.services.rag.indexers.rules_indexer import upsert_rule
                        await upsert_rule(rule.id, db)
                    except Exception as e:
                        logger.warning(f"RAG upsert for new rule failed (non-fatal): {e}")
                except Exception as e:
                    logger.error(f"Failed to save rule: {e}")
                    result["rules_failed"] += 1
                    result["errors"].append(str(e))

            # 3. Coarse-grained backfill: associate every passage of this
            # document with every rule extracted from it. The chat layer
            # then quotes the most-similar passage at query time. A
            # finer-grained per-passage attribution can replace this if/when
            # the LLM is asked to cite the source passage index per rule.
            if indexed_passages and created_rule_ids:
                try:
                    from app.services.rag.indexers.source_docs_indexer import link_rules_to_passage
                    for passage_id, _ in indexed_passages:
                        await link_rules_to_passage(passage_id, created_rule_ids)
                except Exception as e:
                    logger.warning(f"RAG link_rules_to_passage failed (non-fatal): {e}")

            result["success"] = result["rules_created"] > 0
            logger.info(f"Generated {result['rules_created']} rules from document")
            return result

        except Exception as e:
            logger.error(f"Rule generation failed: {e}")
            result["errors"].append(f"LLM rule extraction failed: {str(e)}")
            return result


# Singleton instance
rule_generator_service = RuleGeneratorService()
