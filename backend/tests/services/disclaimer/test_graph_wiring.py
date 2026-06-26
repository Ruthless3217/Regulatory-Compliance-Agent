from app.services.agents.compliance.engine import ComplianceEngine


def test_disclosure_unavailable_is_needs_review():
    assert "disclosure_unavailable" in ComplianceEngine._NEEDS_REVIEW_REASONS


def test_disclosure_unavailable_blocks_persist():
    state = {"chunks": [{"text": "x"}], "status": "completed",
             "metadata": {"degraded": "disclosure_unavailable"}}
    can_persist, reason = ComplianceEngine.evaluate_persistability(state)
    assert can_persist is False
    assert reason == "disclosure_unavailable"
    assert reason in ComplianceEngine._NEEDS_REVIEW_REASONS  # ties the test to the actual Task-8 change, not just the any-degraded short-circuit


def test_graph_has_disclosure_node():
    from app.services.agents.orchestrator import ComplianceOrchestrator
    g = ComplianceOrchestrator()._build_graph()
    graph = g.get_graph()
    assert "disclosure_node" in graph.nodes
    edges = [(e.source, e.target) for e in graph.edges]
    assert ("analysis_node", "disclosure_node") in edges
    assert ("disclosure_node", "scoring_node") in edges
    assert ("analysis_node", "scoring_node") not in edges  # critical: disclosure must be ON the path, not a bypassed parallel branch
