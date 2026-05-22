import logging
from typing import Dict, Any, Optional
from langgraph.graph import StateGraph, END, START
from langchain_core.runnables import RunnableConfig

from .graph.state import ComplianceState
from .graph.nodes import (
    preprocess_node,
    dispatch_node,
    analysis_node,
    scoring_node,
    refinement_node
)
from .graph.context import GraphContext
from ...config import settings

logger = logging.getLogger(__name__)


class ComplianceOrchestrator:
    """
    The Brain: Orchestrates the Regulatory Compliance Multi-Agent System.
    Manages the lifecycle of the LangGraph state machine.
    """

    def __init__(self):
        self.redis_url = settings.redis_url
        self.checkpointer = None
        self._graph = None
        self._checkpointer_initialized = False

    @property
    def graph(self):
        """Constructs the graph lazily if not already built."""
        if self._graph is None:
            self._graph = self._build_graph()
        return self._graph

    def _build_graph(self):
        """Constructs the compliance state graph."""
        workflow = StateGraph(ComplianceState)

        # Add Nodes
        workflow.add_node("preprocess_node", preprocess_node)
        workflow.add_node("dispatch_node", dispatch_node)
        workflow.add_node("analysis_node", analysis_node)
        workflow.add_node("scoring_node", scoring_node)
        workflow.add_node("refinement_node", refinement_node)

        # Define Edges: linear pipeline
        workflow.add_edge(START, "preprocess_node")
        workflow.add_edge("preprocess_node", "dispatch_node")
        workflow.add_edge("dispatch_node", "analysis_node")
        workflow.add_edge("analysis_node", "scoring_node")
        workflow.add_edge("scoring_node", "refinement_node")
        workflow.add_edge("refinement_node", END)

        # Configure Persistence (try Redis, fallback to memory)
        try:
            from langgraph.checkpoint.memory import MemorySaver
            self.checkpointer = MemorySaver()
            self._checkpointer_initialized = True
        except Exception as e:
            logger.warning(f"Checkpointer setup failed: {e}")
            self.checkpointer = None

        # Compile with interrupt_before for HITL
        return workflow.compile(
            checkpointer=self.checkpointer,
            interrupt_before=["refinement_node"]
        )

    async def _ensure_checkpointer_setup(self):
        """Initialize Redis checkpointer if available, fallback to memory."""
        if self._checkpointer_initialized:
            return

        try:
            from langgraph.checkpoint.redis import AsyncRedisSaver
            from ..cache.redis_client import get_redis

            logger.info("Connecting orchestrator to shared Redis client...")
            conn = await get_redis()
            self.checkpointer = AsyncRedisSaver(redis_client=conn)

            if hasattr(self.checkpointer, 'setup'):
                await self.checkpointer.setup()
                logger.info("Redis checkpointer indexes initialized")
            self._checkpointer_initialized = True
            # Rebuild graph with Redis checkpointer
            self._graph = self._build_graph()

        except Exception as e:
            logger.warning(f"Failed to setup Redis checkpointer: {e}. Using MemorySaver.")
            if not self.checkpointer:
                from langgraph.checkpoint.memory import MemorySaver
                self.checkpointer = MemorySaver()
            self._checkpointer_initialized = True
            self._graph = self._build_graph()

    async def run_workflow(self, initial_state: ComplianceState, config: RunnableConfig):
        """Executes the compliance workflow."""
        await self._ensure_checkpointer_setup()
        return await self.graph.ainvoke(initial_state, config=config)

    async def get_state(self, config: RunnableConfig):
        """Gets current graph state."""
        await self._ensure_checkpointer_setup()
        return await self.graph.aget_state(config)

    async def resume_workflow(self, config: RunnableConfig, feedback: str = None):
        """Resumes the workflow after a HITL interrupt."""
        await self._ensure_checkpointer_setup()
        if feedback:
            await self.graph.aupdate_state(config, {"user_feedback": feedback})
        return await self.graph.ainvoke(None, config=config)


# Singleton orchestrator instance
orchestrator = ComplianceOrchestrator()
