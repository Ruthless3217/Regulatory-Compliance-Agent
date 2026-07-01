from abc import ABC, abstractmethod
from typing import List, Any
import logging

logger = logging.getLogger(__name__)


class ComplianceAgent(ABC):
 """
 Abstract Base Class for specialized compliance sub-agents.
 Each agent handles a specific category of rules.
 """

 @abstractmethod
 async def analyze(self, content: str, rules: list, execution_id: str = None, db: Any = None):
 """Analyze content against a specific set of rules."""
 pass

 @property
 @abstractmethod
 def category(self) -> str:
 """The category of rules this agent handles (e.g., 'regulatory', 'brand')."""
 pass

 def _record_trace(self, db: Any, execution_id: str, step: str,
 thought: str = None, action: str = None,
 action_input: Any = None, observation: str = None):
 """Helper to record reasoning traces for observability."""
 if not db or not execution_id:
 return
 try:
 from app.models.agent_trace import AgentTrace
 import uuid

 if isinstance(execution_id, str):
 uid = uuid.UUID(execution_id)
 else:
 uid = execution_id

 trace = AgentTrace(
 execution_id=uid,
 step_number=step,
 thought=thought,
 action=action,
 action_input=action_input,
 observation=observation
 )
 db.add(trace)
 db.commit()
 except Exception as e:
 logger.error(f"Failed to record trace: {e}")
