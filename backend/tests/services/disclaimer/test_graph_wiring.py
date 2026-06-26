from app.services.agents.compliance.engine import ComplianceEngine


def test_disclosure_unavailable_is_needs_review():
    assert "disclosure_unavailable" in ComplianceEngine._NEEDS_REVIEW_REASONS


def test_disclosure_unavailable_blocks_persist():
    state = {"chunks": [{"text": "x"}], "status": "completed",
             "metadata": {"degraded": "disclosure_unavailable"}}
    can_persist, reason = ComplianceEngine.evaluate_persistability(state)
    assert can_persist is False
    assert reason == "disclosure_unavailable"


def test_graph_has_disclosure_node():
    from app.services.agents.orchestrator import ComplianceOrchestrator
    g = ComplianceOrchestrator()._build_graph()
    assert "disclosure_node" in g.get_graph().nodes
