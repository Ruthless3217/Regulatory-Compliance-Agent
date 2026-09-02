"""required_disclosures: disclosure_node's per-doc obligation summary must
actually reach analysis_runs.run_metadata (the audit-trail claim in its
docstring), not just be built and discarded.
"""
from app.services.agents.compliance.engine import ComplianceEngine


def test_required_disclosures_is_whitelisted_into_run_metadata():
    summary = [{"disclaimer_id": "d1", "status": "missing", "similarity": 0.1}]
    final_state = {"metadata": {"required_disclosures": summary}}

    out = ComplianceEngine.run_metadata_from_state(final_state)

    assert out.get("required_disclosures") == summary
