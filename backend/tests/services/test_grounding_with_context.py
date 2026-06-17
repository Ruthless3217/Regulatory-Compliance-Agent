from app.services.agents.graph.nodes import verify_evidence_grounding


def test_quote_only_in_context_is_dropped():
    focal = "100% Guaranteed Early Income for your child."
    violations = [
        {"current_text": "Returns are not guaranteed", "description": "from a NEIGHBOR chunk, not focal"},
        {"current_text": "100% Guaranteed Early Income", "description": "real focal quote"},
    ]
    kept = verify_evidence_grounding(violations, focal)
    kept_texts = [v["current_text"] for v in kept]
    assert "100% Guaranteed Early Income" in kept_texts          # real focal quote kept
    assert "Returns are not guaranteed" not in kept_texts        # context-only quote dropped


def test_structural_empty_current_text_is_kept():
    focal = "Some heading"
    violations = [{"current_text": "", "description": "structural/novel finding has no quote"}]
    kept = verify_evidence_grounding(violations, focal)
    assert len(kept) == 1  # empty current_text is not treated as fabricated
