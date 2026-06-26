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


import asyncio
from unittest.mock import patch
from app.services.agents.graph.nodes import disclosure_node


def _chunks(text):
    return [{"text": text, "metadata": {}}]


def test_node_flags_missing_ulip_and_tax(monkeypatch):
    monkeypatch.setattr("app.config.settings.disclosure_check_enabled", True)
    monkeypatch.setattr("app.config.settings.disclosure_llm_backstop_enabled", False)
    doc = ("Invest in our ULIP plan. Save tax under Section 80C. "
           "Enjoy guaranteed loyalty additions.")
    state = {"chunks": _chunks(doc), "metadata": {"product_match": []}}
    out = asyncio.run(disclosure_node(state))
    ids = {v["violation_metadata"]["disclaimer_id"] for v in out["violations"]}
    assert "ulip_risk" in ids          # keyword 'ULIP'
    assert "tax_123_80c" in ids        # 'Section 80C'
    assert "guaranteed" in ids
    # all are document-level findings with verbatim fixes
    assert all(v["suggested_fix"] for v in out["violations"])


def test_node_noop_when_disabled(monkeypatch):
    monkeypatch.setattr("app.config.settings.disclosure_check_enabled", False)
    out = asyncio.run(disclosure_node({"chunks": _chunks("ULIP Section 80C"), "metadata": {}}))
    assert out == {}


def test_node_fail_closed_when_registry_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr("app.config.settings.disclosure_check_enabled", True)
    from app.services.disclaimer import registry as reg_mod
    reg_mod._reset_singleton_for_testing()
    monkeypatch.setattr("app.config.settings.disclaimers_dir", str(tmp_path / "empty_missing"))
    out = asyncio.run(disclosure_node({"chunks": _chunks("ULIP"), "metadata": {}}))
    assert out["metadata"]["degraded"] == "disclosure_unavailable"
    reg_mod._reset_singleton_for_testing()
