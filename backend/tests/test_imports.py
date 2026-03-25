"""
Quick validation test to check all core imports and structure.
Run with: python -m pytest tests/test_imports.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_schemas_import():
    """Test compliance schemas import."""
    from app.schemas.compliance_schemas import ViolationSchema, ComplianceAnalysisResult
    assert ViolationSchema is not None
    assert ComplianceAnalysisResult is not None


def test_models_import():
    """Test all models import."""
    from app.models import (
        User, Submission, Rule, ComplianceCheck,
        Violation, ContentChunk, AgentExecution, ComplianceState
    )
    assert Submission is not None
    assert Rule is not None


def test_scoring_service():
    """Test scoring service logic without DB."""
    from app.services.agents.compliance.scoring import ScoringService

    violations = [
        {"category": "regulatory", "severity": "critical", "description": "Missing disclaimer"},
        {"category": "regulatory", "severity": "medium", "description": "Font too small"},
    ]

    scores = ScoringService.calculate_scores(violations, db=None, categories=["regulatory"])
    assert "overall" in scores
    assert "grade" in scores
    assert "status" in scores
    assert scores["overall"] >= 0
    assert scores["overall"] <= 100
    assert scores["grade"] in ["A", "B", "C", "D", "F"]


def test_scoring_no_violations():
    """Test scoring with no violations returns 100."""
    from app.services.agents.compliance.scoring import ScoringService

    scores = ScoringService.calculate_scores([], db=None, categories=["regulatory"])
    assert scores["overall"] == 100.0
    assert scores["grade"] == "A"


def test_preprocessing_chunking():
    """Test text chunking without DB."""
    from app.database import SessionLocal
    # Test that the service can be instantiated
    from app.services.preprocessing_service import ContextEngineeringService

    # Test paragraph chunking directly
    service = ContextEngineeringService.__new__(ContextEngineeringService)
    chunks = service._chunk_by_paragraphs("This is a test.\n\nSecond paragraph.", "text")
    assert len(chunks) >= 1
    assert all("text" in c for c in chunks)


def test_graph_state_structure():
    """Test graph state TypedDict structure."""
    from app.services.agents.graph.state import ComplianceState
    # Verify it's a TypedDict with required keys
    assert hasattr(ComplianceState, '__annotations__')
    annotations = ComplianceState.__annotations__
    assert "submission_id" in annotations
    assert "violations" in annotations
    assert "scores" in annotations
    assert "chunks" in annotations


if __name__ == "__main__":
    test_schemas_import()
    test_scoring_service()
    test_scoring_no_violations()
    test_graph_state_structure()
    print("✅ All import tests passed!")
