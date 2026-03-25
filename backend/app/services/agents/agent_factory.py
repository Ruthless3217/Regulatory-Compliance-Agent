from .base_agent import ComplianceAgent
from .standard_agent import StandardComplianceAgent
from ...services.preprocessing_service import ContextEngineeringService


class AgentFactory:
    """
    Factory for creating specialized compliance agents dynamically.
    """

    @staticmethod
    def create_agent(category: str, context_service: ContextEngineeringService) -> ComplianceAgent:
        """
        Create a compliance agent for a specific category.
        All regulatory compliance categories use StandardComplianceAgent.
        """
        return StandardComplianceAgent(category, context_service)
