"""Retrieval debugger persistence (RETRIEVAL_RCA.md §4, deliverable 7).

The dispatch node computes metadata["retrieval_debug"] (scope + per-candidate
accept/reject with reasons). That payload must survive the run: analysis_runs
gains a JSONB run_metadata column, populated at close_run from a whitelisted,
size-capped extract of the final graph state.
"""
from app.models.analysis_run import AnalysisRun
from app.services.agents.compliance.engine import ComplianceEngine


def test_analysis_run_has_run_metadata_column():
    col = AnalysisRun.__table__.columns.get("run_metadata")
    assert col is not None, "analysis_runs.run_metadata missing (migration 0022)"
    assert col.nullable


def test_run_metadata_extract_whitelists_observability_keys():
    state = {
        "metadata": {
            "retrieval_debug": {"scope": {"categories": ["term"]}, "rejected_total": 2},
            "grounding_mix": {"precedent": 3, "disclosure": 1},
            "rag_degraded": False,
            "degraded": None,
            "product_match": [{"uin": "116N165V01", "product_name": "Saral Jeevan",
                               "confidence": 1.0, "method": "uin_regex",
                               "ambiguous": False, "candidates": []}],
            "analysis_failed_chunks": 0,
            "huge_irrelevant_blob": "x" * 100000,
        },
    }
    extract = ComplianceEngine.run_metadata_from_state(state)
    assert extract["retrieval_debug"]["rejected_total"] == 2
    assert extract["grounding_mix"] == {"precedent": 3, "disclosure": 1}
    assert extract["product_match"][0]["uin"] == "116N165V01"
    assert "huge_irrelevant_blob" not in extract


def test_run_metadata_extract_handles_empty_state():
    assert ComplianceEngine.run_metadata_from_state({}) == {}
    assert ComplianceEngine.run_metadata_from_state({"metadata": {}}) == {}
