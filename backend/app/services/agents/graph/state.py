from typing import TypedDict, List, Dict, Any, Optional, Annotated
import operator
from langchain_core.messages import BaseMessage


class ComplianceState(TypedDict):
    """
    State of the compliance orchestration graph.
    Acts as a 'Stateless Reducer' where typed updates are merged.
    """
    submission_id: str
    user_id: Optional[str]

    # Document Content (Librarian's Output)
    chunks: List[Dict[str, Any]]

    # Rules (Teacher's Output) — flat: category -> all active rules
    active_rules: Dict[str, List[Dict[str, Any]]]

    # RAG: per-chunk per-category top-K rules. Set by dispatch_node when the
    # rules retriever is available; analysis_node prefers this over active_rules.
    # Shape: { chunk_id: { category: [rule_dict, ...] } }
    chunk_rules: Dict[str, Dict[str, List[Dict[str, Any]]]]

    # Precedent path: per-chunk retrieved reviewer-decision examples.
    # Set by dispatch_node; analysis_node grades each chunk against these.
    # Shape: { chunk_id: [precedent_dict, ...] }
    retrieved_examples: Dict[str, List[Dict[str, Any]]]

    # Product-doc grounding (2026-06-22). Set by dispatch_node when the
    # submission matched ≥1 approved product (metadata.product_match).
    # product_facts: deterministic fact cards, per DOCUMENT (same for all chunks).
    # product_passages: approved brochure passages, per CHUNK.
    product_facts: List[Dict[str, Any]]
    product_passages: Dict[str, List[Dict[str, Any]]]

    # Analysis Results - Using operator.add to append violations from parallel agents
    violations: Annotated[List[Dict[str, Any]], operator.add]

    # Track which agents were spawned
    active_agents: Annotated[List[str], operator.add]

    # Scoring
    scores: Dict[str, Any]

    # Status & Metadata
    status: str
    metadata: Dict[str, Any]

    # History & Chat (for HITL)
    messages: Annotated[List[BaseMessage], operator.add]

    # HITL feedback
    user_feedback: Optional[str]
