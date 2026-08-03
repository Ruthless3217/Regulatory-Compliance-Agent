"""Evidence-gated disclaimer matching (Past Performance false-mismatch fix).

Root cause (ROOT_CAUSE_ANALYSIS.md): a document whose extraction LOST the
disclaimer scored ~0.50 on fuzz.partial_ratio — pure fuzzy-floor noise — and was
reported as "present but altered". The matcher must only claim "altered" when
there is a genuine aligned span sharing the required text's distinctive tokens;
otherwise the honest verdict is "missing". It must also expose the evidence
(matched span, raw + normalised similarity, method) so every verdict is
explainable.
"""
import pytest

from app.services.disclaimer.matcher import classify, match_details, normalize

REQUIRED = "**Past performance is not indicative of future performance."
PRESENT_T = 0.85
ALTERED_T = 0.45

# Body copy that legitimately carries a past-performance OBLIGATION but makes no
# attempt at the disclaimer wording (mirrors the frontend sample + the failing
# creative's extracted text after the footer was dropped).
NO_ATTEMPT_DOC = (
    "Our brand-new ULIP scheme guarantees 25% returns every year - no market risk! "
    "Bajaj is India's No.1 life insurer and our policy is the cheapest in the market. "
    "Past performance: between 2022 and 2024 our equity-linked fund delivered an "
    "average of 22% per annum. Withdraw anytime - there are no lock-ins."
)


def _classify(doc):
    return classify(REQUIRED, [], doc, PRESENT_T, ALTERED_T)


# --- honest missing-vs-altered ------------------------------------------------

def test_fuzzy_floor_noise_reports_missing_not_altered():
    """A doc with no genuine attempt at the disclaimer must be 'missing', even
    when partial_ratio noise lands inside the altered band (the 0.50 bug)."""
    status, sim = _classify(NO_ATTEMPT_DOC)
    assert ALTERED_T <= sim < PRESENT_T, "fixture must land in the altered band"
    assert status == "missing"


def test_unrelated_document_reports_missing():
    status, _ = _classify(
        "Term insurance secures your family's future. Buy protection today "
        "with flexible premium payment options and wide coverage."
    )
    assert status == "missing"


def test_genuine_alteration_still_reports_altered_missing_not():
    # Dangerous alteration: drops the negation — must stay flagged as altered.
    status, sim = _classify(
        "Please note: past performance is indicative of future performance "
        "of our funds."
    )
    assert status == "altered"


def test_genuine_alteration_still_reports_altered_may_indicate():
    status, _ = _classify(
        "Past performance may indicate the future performance of the fund."
    )
    assert status == "altered"


def test_genuine_paraphrase_still_reports_altered():
    status, _ = _classify(
        "Past performance is not a guarantee of future results."
    )
    assert status == "altered"


# --- unchanged correct behaviour (regression matrix) --------------------------

@pytest.mark.parametrize("doc", [
    "Past performance is not indicative of future performance.",
    "**Past performance is not indicative of future performance.**",
    "PAST PERFORMANCE IS NOT INDICATIVE OF FUTURE PERFORMANCE.",
    "Past performance is not\nindicative of future performance.",
    "Fund returns were strong.\n\nPast performance is not indicative of future performance.\n\nCall us today.",
    "“Past performance is not indicative of future performance.”",  # smart quotes
])
def test_approved_wording_is_present(doc):
    status, sim = _classify(doc)
    assert status == "present"
    assert sim >= 0.999


def test_fragment_shorter_than_required_exact():
    status, sim = _classify("Past performance is not indicative of future performance")
    assert status == "present"


def test_disclaimer_split_across_chunk_boundary():
    """disclosure_node matches on '\n'.join(chunk texts) — a sentence split by
    the chunker must still classify as present after reassembly."""
    chunk_a = "Fund facts and returns history. Past performance is not"
    chunk_b = "indicative of future performance. Contact your advisor."
    status, sim = _classify("\n".join([chunk_a, chunk_b]))
    assert status == "present"
    assert sim >= 0.999


def test_disclaimer_followed_by_comment_text_still_present():
    """Reviewer-style trailing notes next to the disclaimer must not break the
    match of the visible wording."""
    status, _ = _classify(
        "Past performance is not indicative of future performance. "
        "[Reviewer note: verify font size meets IRDAI minimum]"
    )
    assert status == "present"


# --- explainability payload ---------------------------------------------------

def test_match_details_exposes_evidence_for_present():
    d = match_details(REQUIRED, [], "Intro. Past performance is not indicative "
                      "of future performance. Outro.", PRESENT_T, ALTERED_T)
    assert d.status == "present"
    assert d.match_method == "exact_normalized_match"
    assert "past performance is not indicative of future performance" in d.evidence_span
    assert d.similarity == 1.0
    assert 0.0 <= d.raw_similarity <= 1.0
    assert d.decision_trace, "decision trace must not be empty"


def test_match_details_missing_has_no_fabricated_evidence():
    d = match_details(REQUIRED, [], NO_ATTEMPT_DOC, PRESENT_T, ALTERED_T)
    assert d.status == "missing"
    assert d.token_overlap <= 0.5
    assert d.match_method in ("windowed_partial_ratio", "direct_ratio")


def test_match_details_altered_names_the_span():
    doc = "Note that past performance may indicate the future performance of the fund."
    d = match_details(REQUIRED, [], doc, PRESENT_T, ALTERED_T)
    assert d.status == "altered"
    assert d.token_overlap > 0.5
    assert d.evidence_span  # the aligned window is reported as evidence
    assert "performance" in d.evidence_span


def test_match_details_reports_both_similarities():
    d = match_details(REQUIRED, [], "Past performance is not indicative of "
                      "future performance.", PRESENT_T, ALTERED_T)
    # Normalised similarity is exact; raw similarity is high but need not be 1.0
    # (the registry text carries the '**' artefact on the raw side).
    assert d.similarity == 1.0
    assert d.raw_similarity >= 0.9


# --- anchors keep their fail-closed semantics ---------------------------------

def test_absent_anchor_still_missing_when_body_weak():
    status, _ = classify(REQUIRED, ["not indicative"], NO_ATTEMPT_DOC, PRESENT_T, ALTERED_T)
    assert status == "missing"


def test_absent_anchor_altered_when_body_strong():
    doc = "Past performance is n0t indicative of future performance."  # anchor text broken
    status, sim = classify(REQUIRED, ["not indicative"], doc, PRESENT_T, ALTERED_T)
    assert status == "altered"
    assert sim >= PRESENT_T


# --- registry hygiene ----------------------------------------------------------

def test_registry_records_free_of_ingestion_artifacts():
    """Spreadsheet footnote markers ('**') must not survive into the mandated
    wording or the display type — they corrupt the UI verdict and suggested fix."""
    from pathlib import Path
    from app.services.disclaimer.registry import DisclaimerRegistry
    reg = DisclaimerRegistry(Path(__file__).resolve().parents[1] / "data" / "disclaimers")
    assert reg.loaded_ok
    for disc in reg.all():
        assert "**" not in disc.type, f"{disc.id}: type carries footnote artefact"
        assert "**" not in disc.text, f"{disc.id}: text carries footnote artefact"


# --- verdict payload explainability -------------------------------------------

def _payload(doc, provenance, trigger_source):
    from pathlib import Path
    from app.services.agents.graph.nodes import _disclosure_finding_to_violation
    from app.services.disclaimer.registry import DisclaimerRegistry
    reg = DisclaimerRegistry(Path(__file__).resolve().parents[1] / "data" / "disclaimers")
    d = reg.get("past_performance")
    md = match_details(d.text, d.anchors, doc, d.present_threshold, d.altered_threshold)
    v = _disclosure_finding_to_violation(
        d, status=md.status, similarity=md.similarity, provenance=provenance,
        confidence=0.85, details=md, trigger_source=trigger_source,
    )
    return md, v


def test_missing_verdict_description_is_honest():
    """A fuzzy-floor 'not found' must never render as 'present but altered'."""
    md, v = _payload(NO_ATTEMPT_DOC, "llm:past_performance", "llm")
    assert md.status == "missing"
    assert "present but altered" not in v["description"].lower()
    assert "missing" in v["description"].lower()


def test_verdict_metadata_carries_explanation():
    md, v = _payload(NO_ATTEMPT_DOC, "llm:past_performance", "llm")
    meta = v["violation_metadata"]
    assert meta["match_method"] == md.match_method
    assert meta["normalized_similarity"] == pytest.approx(md.similarity)
    assert meta["raw_similarity"] == pytest.approx(md.raw_similarity)
    assert meta["evidence_span"] == md.evidence_span
    assert meta["token_overlap"] == pytest.approx(md.token_overlap)
    assert meta["decision_trace"] == md.decision_trace
    assert meta["counterfactual"]


def test_verdict_provenance_hybrid_for_llm_trigger():
    _, v = _payload(NO_ATTEMPT_DOC, "llm:past_performance", "llm")
    assert v["violation_metadata"]["verdict_provenance"] == "hybrid"


def test_verdict_provenance_deterministic_for_keyword_trigger():
    _, v = _payload(NO_ATTEMPT_DOC, "matched 'past performance'", "deterministic")
    assert v["violation_metadata"]["verdict_provenance"] == "deterministic_rule"


def test_altered_verdict_names_evidence_span():
    doc = "Past performance may indicate the future performance of the fund."
    md, v = _payload(doc, "matched 'past performance'", "deterministic")
    assert md.status == "altered"
    assert v["violation_metadata"]["evidence_span"]
    # The reviewer must be able to SEE what the engine matched.
    assert md.evidence_span[:20] in v["description"] or "evidence" in v["description"].lower()
