from typing import List, Any
from .base_agent import ComplianceAgent
from ...schemas.compliance_schemas import ComplianceAnalysisResult
from ...services.llm_service import llm_service
from ...services.preprocessing_service import ContextEngineeringService


class StandardComplianceAgent(ComplianceAgent):
 """
 Standard compliance agent implementation.
 Uses Context Engineering to build prompts and LLM for structured output.
 """

 def __init__(self, category: str, context_service: ContextEngineeringService):
 self._category = category
 self.context_service = context_service
 self.system_prompt = (
 f"You are a specialist {self.category} regulatory compliance agent. "
 f"Analyze the provided content against the rules strictly. "
 f"Return ONLY valid JSON matching the required schema."
 )

 @property
 def category(self) -> str:
 return self._category

 @property
 def name(self) -> str:
 return self._category

 async def analyze(
 self,
 content: str,
 rules: list,
 execution_id: str = None,
 db: Any = None
 ) -> ComplianceAnalysisResult:
 """Analyze content against compliance rules using LLM."""

 # Step 1: Planning trace
 self._record_trace(
 db, execution_id, "Step 1: Planning",
 thought=f"Analyzing {len(rules)} {self.category} rules against content.",
 action="ContextEngineeringService.create_compliance_prompts"
 )

 # Build prompt
 rules_dict = {self.category: rules}
 prompt = self.context_service.create_compliance_prompts(content, rules_dict)

 context = {
 "agent_category": self.category,
 "rules_count": len(rules)
 }

 # Step 2: LLM call trace
 self._record_trace(
 db, execution_id, "Step 2: LLM Call",
 thought="Prompt constructed. Sending to LLM for structured compliance analysis.",
 action="llm_service.generate_structured_response"
 )

 # Call LLM
 response = await llm_service.generate_structured_response(
 prompt=prompt,
 output_model=ComplianceAnalysisResult,
 system_prompt=self.system_prompt,
 context=context,
 execution_id=execution_id,
 db=db,
 tool_name=f"{self.name}_analysis"
 )

 # Step 3: Result trace
 self._record_trace(
 db, execution_id, "Step 3: Result",
 thought="Analysis complete.",
 observation=f"Found {len(response.violations)} violations."
 )

 return response
