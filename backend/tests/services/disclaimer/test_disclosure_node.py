import asyncio

from app.services.disclaimer.registry import Disclaimer
from app.services.agents.graph.nodes import (
    _disclosure_finding_to_violation,
    disclosure_node,
)

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


def _chunks(text):
    return [{"text": text, "metadata": {}}]


def test_node_flags_missing_ulip_and_tax(monkeypatch):
    from pathlib import Path
    from app.services.disclaimer import registry as reg_mod
    _disclaimers_dir = Path(__file__).resolve().parents[3] / "data" / "disclaimers"
    monkeypatch.setattr("app.config.settings.disclaimers_dir", str(_disclaimers_dir))
    monkeypatch.setattr("app.config.settings.disclosure_check_enabled", True)
    monkeypatch.setattr("app.config.settings.disclosure_llm_backstop_enabled", False)
    reg_mod._reset_singleton_for_testing()
    try:
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
    finally:
        reg_mod._reset_singleton_for_testing()


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


def test_node_llm_backstop_failure_degrades_recall(monkeypatch):
    """LLM backstop raises → recall_degraded=True, but deterministic findings still emit."""
    from pathlib import Path
    from app.services.disclaimer import registry as reg_mod
    _disclaimers_dir = Path(__file__).resolve().parents[3] / "data" / "disclaimers"
    monkeypatch.setattr("app.config.settings.disclaimers_dir", str(_disclaimers_dir))
    monkeypatch.setattr("app.config.settings.disclosure_check_enabled", True)
    monkeypatch.setattr("app.config.settings.disclosure_llm_backstop_enabled", True)
    reg_mod._reset_singleton_for_testing()
    try:
        async def boom(_doc, _types):
            raise RuntimeError("llm backstop down")
        monkeypatch.setattr("app.services.agents.graph.nodes._disclosure_llm_call", boom)
        doc = "Invest in our ULIP plan. Save tax under Section 80C."
        out = asyncio.run(disclosure_node({"chunks": [{"text": doc}], "metadata": {"product_match": []}}))
        assert out["metadata"].get("disclosure_recall_degraded") is True
        ids = {v["violation_metadata"]["disclaimer_id"] for v in out["violations"]}
        assert "ulip_risk" in ids  # deterministic findings still emit despite the LLM failure
    finally:
        reg_mod._reset_singleton_for_testing()


def test_node_llm_backstop_adds_obligation_at_0_85_confidence(monkeypatch):
    """LLM backstop returns an obligation the regex would miss → surfaced at confidence 0.85."""
    from pathlib import Path
    from app.services.disclaimer import registry as reg_mod
    _disclaimers_dir = Path(__file__).resolve().parents[3] / "data" / "disclaimers"
    monkeypatch.setattr("app.config.settings.disclaimers_dir", str(_disclaimers_dir))
    monkeypatch.setattr("app.config.settings.disclosure_check_enabled", True)
    monkeypatch.setattr("app.config.settings.disclosure_llm_backstop_enabled", True)
    reg_mod._reset_singleton_for_testing()
    try:
        async def fake_llm(_doc, _types):
            return ["past_performance"]            # an obligation the regex would miss
        monkeypatch.setattr("app.services.agents.graph.nodes._disclosure_llm_call", fake_llm)
        doc = "Our flagship fund delivered strong double-digit growth last year."
        out = asyncio.run(disclosure_node({"chunks": [{"text": doc}], "metadata": {"product_match": [{"uin": "X"}]}}))
        pp = [v for v in out["violations"] if v["violation_metadata"]["disclaimer_id"] == "past_performance"]
        assert pp, "LLM-only past_performance obligation should be surfaced"
        assert pp[0]["confidence"] == 0.85
        assert out["metadata"].get("disclosure_recall_degraded") is not True
    finally:
        reg_mod._reset_singleton_for_testing()


def test_node_fail_closed_when_registry_has_corrupted_file(monkeypatch, tmp_path):
    """A registry with one corrupted file (partial load) must route to needs_review.

    This is the critical fail-closed guarantee: a registry that silently dropped an
    obligation (e.g. ulip_risk.json is corrupted) must NOT certify a document clean.
    The node must degrade to disclosure_unavailable WITHOUT making any LLM call —
    purely offline via loaded_ok=False.
    """
    import json as _json

    # One malformed JSON (will fail parse) alongside one syntactically valid disclaimer.
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    valid = {
        "id": "ulip_risk",
        "type": "ULIP Disclaimer",
        "text": "IN THIS POLICY, THE INVESTMENT RISK IN THE INVESTMENT PORTFOLIO IS BORNE BY THE POLICYHOLDER.",
        "anchors": ["IN THIS POLICY, THE INVESTMENT RISK"],
        "severity": "critical",
        "altered_severity": "high",
        "triggers": {"product_lines": ["ulip"], "keywords_regex": ["\\bULIP\\b"], "llm_obligation_type": "ulip_risk"},
        "match": {"present_threshold": 0.85, "altered_threshold": 0.45},
        "precedence": 100,
        "source": "irdai",
    }
    (tmp_path / "ulip_risk.json").write_text(_json.dumps(valid), encoding="utf-8")

    monkeypatch.setattr("app.config.settings.disclosure_check_enabled", True)
    monkeypatch.setattr("app.config.settings.disclosure_llm_backstop_enabled", False)
    monkeypatch.setattr("app.config.settings.disclaimers_dir", str(tmp_path))

    from app.services.disclaimer import registry as reg_mod
    reg_mod._reset_singleton_for_testing()
    try:
        out = asyncio.run(disclosure_node({"chunks": _chunks("ULIP"), "metadata": {}}))
        assert out["metadata"]["degraded"] == "disclosure_unavailable"
    finally:
        reg_mod._reset_singleton_for_testing()
