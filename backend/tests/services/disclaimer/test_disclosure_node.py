from app.services.disclaimer.registry import Disclaimer
from app.services.agents.graph.nodes import _disclosure_finding_to_violation

D = Disclaimer(
    id="ulip_risk", type="ULIP Disclaimer", text="IN THIS POLICY, THE INVESTMENT RISK ...",
    anchors=["IN THIS POLICY"], severity="critical", altered_severity="high",
    triggers={}, present_threshold=0.85, altered_threshold=0.45, precedence=100, source="x",
)


def test_missing_uses_full_severity_and_verbatim_fix():
    v = _disclosure_finding_to_violation(D, status="missing", similarity=0.1, provenance="product_line=ulip", confidence=1.0)
    assert v["severity"] == "critical"
    assert v["suggested_fix"] == D.text                # verbatim, paste-ready
    assert v["current_text"] == ""                     # document-level omission
    assert v["confidence"] == 1.0
    assert v["suppressed"] is False
    assert v["violation_metadata"]["grounding"] == "disclosure"
    assert v["violation_metadata"]["disclaimer_id"] == "ulip_risk"
    assert v["violation_metadata"]["match_status"] == "missing"


def test_altered_uses_altered_severity():
    v = _disclosure_finding_to_violation(D, status="altered", similarity=0.6, provenance="kw", confidence=0.85)
    assert v["severity"] == "high"
    assert v["confidence"] == 0.85
