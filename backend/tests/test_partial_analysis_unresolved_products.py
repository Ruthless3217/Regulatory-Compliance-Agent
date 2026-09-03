"""A missing fact card is a gap in the evidence, not a hole in the scope.

`evaluate_persistability` treated every `product_unresolved` signal as a hard
refusal. Measured on the real "GEO content for Smart Secure" submission
(8a3c2db4, 27 chunks, 25,445 chars):

    resolved products      116L215V01, 116L205V01
    regulatory scope       {ulip}, resolved=True        <- PROVEN without the gap
    ungrounded UIN         116N216V01 (a combi health component with no own card)
    where it occurs        chunk 24 of 27 = 16.1% of the document
    grounded evidence      27 guardrail obligations + 27 approved passages
    outcome                needs_review, nothing persisted, 38 findings discarded

Two separate defects sat behind that:

  1. `_resolve_product_grounding` returned ([], {}) on ANY unresolved signal, so
     the run that was refused had also been analysed with its fact cards and
     approved wording deliberately withheld — the least-grounded analysis, then
     thrown away.
  2. The refusal conflated "the scope cannot be proven" with "one product's
     evidence is missing". Only the first is unprovable; the second is a
     bounded, locatable gap in a document whose scope is proven.

So the gate now asks the question it was always meant to ask: can the
regulatory scope be established? Scope failures (no resolvable scope, a
declared/detected conflict, an ambiguous UIN, a resolver crash) still refuse.
Evidence gaps grade, carry named warnings, and can never be certified: a warned
run's compliance status is capped below "passed".

The floor under all of it: a run with NO grounded tier at all — no applicable
rule, no precedent, no fact card — is novel-tier LLM opinion, not a regulatory
determination, and still refuses.
"""
import asyncio
import uuid
from pathlib import Path

import pytest

from app.services.agents.compliance.engine import ComplianceEngine
from app.services.agents.graph import nodes as graph_nodes
from app.services.agents.graph.context import GraphContext
from app.services.fact_card_service import FactCardService

CARDS_DIR = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"

# The real document's shape: a named plan whose leaflet references a combi
# health component that has no standalone fact card.
KNOWN_PLUS_UNGROUNDED_RIDER = """Bajaj Life Smart Secure ROP (UIN: 116L215V01)
gives you market linked returns with a return of premium.

Guaranteed returns of 12% every year with zero risk.

Disclaimers: this advertisement is designed for a combination of two individual
products. Bajaj Life Secure Plus - Shield with ROP Variant (UIN:116N216V01).
"""

KNOWN_ONLY = """Bajaj Life Smart Secure ROP (UIN: 116L215V01) gives you market
linked returns. Guaranteed returns of 12% every year with zero risk.
"""

UNKNOWN_UIN_ONLY = """Our new plan (UIN: 116N999V01) guarantees 12% returns
every year with absolutely zero risk to your capital.
"""

GRADEABLE = {"rules": 4, "precedents": 0, "product_facts": 1}


def _state(metadata, *, status="completed", chunks=None):
    return {
        "chunks": chunks or [{"id": "chunk-1", "text": "creative"}],
        "status": status,
        "metadata": metadata,
    }


def _with_evidence(metadata, evidence=None):
    return {**metadata, "grounded_evidence": dict(evidence or GRADEABLE)}


# --------------------------------------------------------------------------
# Step 1 — the gate now separates scope from evidence.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("signal", [
    "rider_uins_without_fact_cards",
    "unknown_uins",
    "declared_products_without_fact_cards",
])
def test_an_evidence_gap_no_longer_refuses_a_document_whose_scope_is_proven(signal):
    state = _state(_with_evidence({
        "product_unresolved": {signal: ["116N216V01"]},
    }))

    assert ComplianceEngine.evaluate_persistability(state) == (True, None)


def test_an_unprovable_scope_still_refuses():
    """`submission_scope` is the one sub-signal that IS a scope failure."""
    state = _state(_with_evidence({
        "product_unresolved": {
            "submission_scope": [
                "no product was resolved and no supported product_line was declared"
            ],
        },
    }))

    assert ComplianceEngine.evaluate_persistability(state) == (
        False, "product_unresolved",
    )


def test_a_scope_failure_beside_an_evidence_gap_still_refuses():
    """The strictest signal in the bucket must decide, not the first key."""
    state = _state(_with_evidence({
        "product_unresolved": {
            "rider_uins_without_fact_cards": ["116N216V01"],
            "submission_scope": ["declared term conflicts with detected scope ['ulip']"],
        },
    }))

    assert ComplianceEngine.evaluate_persistability(state) == (
        False, "product_unresolved",
    )


@pytest.mark.parametrize("metadata,reason", [
    ({"product_ambiguous_uins": ["116L214V01"]}, "product_ambiguous"),
    ({"product_resolution_failed": "RuntimeError"}, "product_resolution_failed"),
    ({"degraded": "rules_unavailable"}, "rules_unavailable"),
    ({"degraded": "disclosure_unavailable"}, "disclosure_unavailable"),
    ({"disclosure_recall_degraded": True}, "disclosure_recall_degraded"),
    ({"analysis_failed_chunks": 2}, "analysis_incomplete"),
])
def test_every_other_refusal_is_unchanged(metadata, reason):
    assert ComplianceEngine.evaluate_persistability(
        _state(_with_evidence(metadata))
    ) == (False, reason)


def test_no_content_and_hard_failure_are_unchanged():
    assert ComplianceEngine.evaluate_persistability(
        {"chunks": [], "status": "completed", "metadata": {}}
    ) == (False, "no_content")
    assert ComplianceEngine.evaluate_persistability(
        _state({}, status="failed")
    ) == (False, "failed")


# --------------------------------------------------------------------------
# Step 7 / 8 — precedent absence and untagged rules are degraded, not fatal.
# --------------------------------------------------------------------------


def test_precedent_absence_alone_no_longer_refuses():
    """0 precedents + real rules + a fact card is a narrower grade, not none."""
    state = _state(_with_evidence(
        {"analysis_warnings": [{"code": "precedent_evidence_unavailable"}]},
        {"rules": 12, "precedents": 0, "product_facts": 1},
    ))

    assert ComplianceEngine.evaluate_persistability(state) == (True, None)


def test_untagged_rule_corpus_alone_no_longer_refuses():
    """Applicability still rejects every untagged rule, per item. What changes
    is only whether that rejection aborts a run whose other tiers are real."""
    state = _state(_with_evidence(
        {"scope_metadata_missing": {"count": 153, "examples": []}},
        {"rules": 0, "precedents": 4, "product_facts": 2},
    ))

    assert ComplianceEngine.evaluate_persistability(state) == (True, None)


def test_a_run_with_no_grounded_tier_at_all_still_refuses():
    """The floor. Rules 0 + precedents 0 + fact cards 0 leaves only the novel
    tier, which is LLM judgment — never a regulatory determination."""
    state = _state(_with_evidence(
        {"product_unresolved": {"rider_uins_without_fact_cards": ["116N216V01"]}},
        {"rules": 0, "precedents": 0, "product_facts": 0},
    ))

    assert ComplianceEngine.evaluate_persistability(state) == (
        False, "no_grounded_evidence",
    )
    assert "no_grounded_evidence" in ComplianceEngine._NEEDS_REVIEW_REASONS


@pytest.mark.parametrize("evidence", [
    {"rules": 1, "precedents": 0, "product_facts": 0},
    {"rules": 0, "precedents": 1, "product_facts": 0},
    {"rules": 0, "precedents": 0, "product_facts": 1},
])
def test_any_single_grounded_tier_clears_the_floor(evidence):
    assert ComplianceEngine.evaluate_persistability(
        _state(_with_evidence({}, evidence))
    ) == (True, None)


@pytest.mark.parametrize("census", [
    pytest.param({}, id="empty-dict"),
    pytest.param({"rules": "0", "precedents": "0", "product_facts": "0"}, id="strings"),
    pytest.param({"rules": "2"}, id="numeric-string"),
    pytest.param({"foo": 1}, id="unknown-key-only"),
    pytest.param([0, 0, 0], id="not-a-dict"),
    pytest.param("rules=1", id="a-string"),
    pytest.param({"rules": -1, "precedents": 0, "product_facts": 0}, id="negative"),
    pytest.param({"rules": True, "precedents": 0, "product_facts": 0}, id="bool-true"),
    pytest.param({"rules": 1.5, "precedents": 0, "product_facts": 0}, id="float"),
    pytest.param({"rules": None, "precedents": None, "product_facts": None}, id="nulls"),
])
def test_a_malformed_evidence_census_refuses_rather_than_passing_through(census):
    """The floor is a safety control, so it must fail closed on anything it
    cannot read. `any(dict.values())` was truthy for "0", for an unknown key,
    for a negative count and for a non-dict — five ways to clear a floor whose
    whole purpose is to stop an ungrounded run being graded."""
    assert ComplianceEngine.evaluate_persistability(
        _state({**{}, "grounded_evidence": census})
    ) == (False, "no_grounded_evidence")


@pytest.mark.parametrize("census", [
    {"rules": 1, "precedents": 0, "product_facts": 0},
    {"rules": 0, "precedents": 4, "product_facts": 0},
    {"rules": 0, "precedents": 0, "product_facts": 2},
    {"rules": 3, "precedents": 2, "product_facts": 1, "future_tier": 9},
])
def test_a_well_formed_census_with_real_evidence_still_grades(census):
    """Hardening must not turn into refusing everything — including a census
    from a newer dispatch that reports a tier this build has never heard of."""
    assert ComplianceEngine.evaluate_persistability(
        _state({**{}, "grounded_evidence": census})
    ) == (True, None)


def test_dispatch_always_reports_what_evidence_it_grounded_with(monkeypatch):
    """The producer side of the floor. The gate refuses on a census of zeros,
    never on a missing census, so the census must always be written — including
    on the path where every retriever failed, which is exactly when it matters.
    """
    from app.services import rule_generator_service as rgs

    monkeypatch.setattr(
        rgs.rule_generator_service, "get_active_rules", lambda _db: {}
    )
    token = GraphContext.set_db_session(_Db([]))
    try:
        out = asyncio.run(graph_nodes.dispatch_node({
            "submission_id": str(uuid.uuid4()),
            "chunks": [{"id": "c0", "chunk_index": 0, "text": "Guaranteed 12%."}],
            "metadata": {},
        }))
    finally:
        GraphContext.reset(token)

    assert out["metadata"]["grounded_evidence"] == {
        "rules": 0, "precedents": 0, "product_facts": 0,
    }
    assert ComplianceEngine.evaluate_persistability({
        "chunks": [{"id": "c0", "text": "x"}], "status": "completed",
        "metadata": out["metadata"],
    }) == (False, "no_grounded_evidence")


def test_a_state_that_never_reported_evidence_is_not_blocked_by_the_floor():
    """The floor judges a measurement, never the absence of one — inventing a
    refusal from an unset key would fail closed on the wrong evidence. The
    producer side is pinned by
    test_dispatch_always_reports_what_evidence_it_grounded_with."""
    assert ComplianceEngine.evaluate_persistability(_state({})) == (True, None)


# --------------------------------------------------------------------------
# The amplifying defect: grounding was withheld from the run it then discarded.
# --------------------------------------------------------------------------


@pytest.fixture
def cards():
    service = FactCardService(CARDS_DIR)
    assert not service.availability_issues, service.availability_issues
    return service


@pytest.fixture
def grounding_env(monkeypatch, cards):
    from app.config import settings
    from app.services import fact_card_service as fcs
    from app.services.rag.retrievers import product_docs_retriever as pdr

    monkeypatch.setattr(settings, "product_grounding_enabled", True)
    monkeypatch.setattr(fcs, "get_fact_card_service", lambda: cards)

    class _NoPassages:
        async def retrieve(self, **_kwargs):
            return []

    monkeypatch.setattr(pdr, "get_product_docs_retriever", lambda: _NoPassages())
    return cards


def test_an_evidence_gap_no_longer_strips_the_resolved_products_grounding(
    grounding_env,
):
    """The measured harm: one ungrounded rider withheld BOTH resolved products'
    authoritative guardrails from every chunk of the document."""
    state = {"metadata": {
        "product_match": [
            {"uin": "116L215V01", "product_name": "Bajaj Life Smart Secure ROP"}
        ],
        "product_grounding_uins": ["116L215V01"],
        "product_unresolved": {"rider_uins_without_fact_cards": ["116N216V01"]},
    }}

    facts, _passages = asyncio.run(
        graph_nodes._resolve_product_grounding(state, [{"id": "c0", "text": "x"}])
    )

    assert [c["uin"] for c in facts] == ["116L215V01"]


def test_the_ungrounded_product_is_never_given_another_products_card(grounding_env):
    """The rider's UIN resolves to its PARENT card inside FactCardService. It
    must not reach the prompt as the rider's own grounding."""
    state = {"metadata": {
        "product_match": [
            {"uin": "116L215V01", "product_name": "Bajaj Life Smart Secure ROP"}
        ],
        "product_grounding_uins": ["116L215V01"],
        "product_unresolved": {"rider_uins_without_fact_cards": ["116N216V01"]},
    }}

    facts, _ = asyncio.run(
        graph_nodes._resolve_product_grounding(state, [{"id": "c0", "text": "x"}])
    )

    assert "116N216V01" not in {c["uin"] for c in facts}


@pytest.mark.parametrize("metadata", [
    {"product_resolution_failed": "RuntimeError"},
    {"product_unresolved": {"submission_scope": ["unsupported declared product_line: x"]}},
    {"product_match": [{"uin": "116L214V01", "ambiguous": True}]},
])
def test_grounding_is_still_withheld_when_identity_or_scope_is_unsafe(
    metadata, grounding_env,
):
    state = {"metadata": {
        "product_match": [
            {"uin": "116L215V01", "product_name": "Bajaj Life Smart Secure ROP"}
        ],
        "product_grounding_uins": ["116L215V01"],
        **metadata,
    }}

    assert asyncio.run(
        graph_nodes._resolve_product_grounding(state, [{"id": "c0", "text": "x"}])
    ) == ([], {})


# --------------------------------------------------------------------------
# Step 9 — warnings: a list, accumulated, never a single overwritten slot.
# --------------------------------------------------------------------------


def test_warnings_accumulate_instead_of_overwriting_each_other():
    """`metadata["degraded"]` is one slot and is last-writer-wins: the real run
    recorded `knowledge_base_empty` over `product_unresolved`. Warnings must not
    inherit that."""
    md = {}
    graph_nodes._add_warning(
        md, "rider_uins_without_fact_cards", {"uins": ["116N216V01"]}
    )
    graph_nodes._add_warning(md, "precedent_evidence_unavailable")
    graph_nodes._add_warning(md, "rule_scope_metadata_incomplete", {"count": 153})

    assert [w["code"] for w in md["analysis_warnings"]] == [
        "rider_uins_without_fact_cards",
        "precedent_evidence_unavailable",
        "rule_scope_metadata_incomplete",
    ]


def test_the_same_warning_is_recorded_once():
    md = {}
    graph_nodes._add_warning(md, "precedent_evidence_unavailable")
    graph_nodes._add_warning(md, "precedent_evidence_unavailable")

    assert len(md["analysis_warnings"]) == 1


def test_warnings_and_evidence_reach_the_durable_run_record():
    audit = ComplianceEngine.run_metadata_from_state({"metadata": {
        "analysis_warnings": [{"code": "rider_uins_without_fact_cards"}],
        "grounded_evidence": GRADEABLE,
    }})

    assert audit["analysis_warnings"] == [{"code": "rider_uins_without_fact_cards"}]
    assert audit["grounded_evidence"] == GRADEABLE


# --------------------------------------------------------------------------
# Step 4 / Design D — the gap is located, not used to exclude content.
# --------------------------------------------------------------------------


def test_an_ungrounded_uin_is_located_in_the_chunks_that_name_it():
    chunks = [
        {"id": "c0", "chunk_index": 0, "text": "Smart Secure ROP gives you returns."},
        {"id": "c1", "chunk_index": 1, "text": "Guaranteed 12% every year."},
        {"id": "c2", "chunk_index": 2, "text": "Disclaimers ... (UIN:116N216V01)."},
    ]

    assert graph_nodes._ungrounded_uin_locations(chunks, ["116N216V01"]) == {
        "116N216V01": [2],
    }


def test_locating_the_gap_does_not_exclude_those_chunks_from_analysis():
    """Design D's exclusion half is refuted by the real document: the ONLY chunk
    naming 116N216V01 is the Disclaimers block — the densest compliance content
    in the file. Marking it must never mean skipping it."""
    chunks = [{"id": "c0", "chunk_index": 0, "text": "Disclaimers (UIN:116N216V01)"}]
    located = graph_nodes._ungrounded_uin_locations(chunks, ["116N216V01"])

    assert located == {"116N216V01": [0]}
    assert [c["id"] for c in chunks] == ["c0"], "chunks are never dropped"


# --------------------------------------------------------------------------
# Step 12 — a degraded result must never be certifiable.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status,expected", [
    ("passed", "flagged"),
    ("flagged", "flagged"),
    ("failed", "failed"),
])
def test_a_warned_run_can_never_be_recorded_as_passed(status, expected):
    capped = ComplianceEngine.cap_status_for_warnings(
        status, [{"code": "rider_uins_without_fact_cards"}]
    )

    assert capped == expected


def test_an_unwarned_run_keeps_its_status():
    assert ComplianceEngine.cap_status_for_warnings("passed", []) == "passed"
    assert ComplianceEngine.cap_status_for_warnings("passed", None) == "passed"


def test_warnings_do_not_invent_a_score_penalty():
    """Not evaluated is not the same as not compliant. The score stays truthful
    about what was actually found; it is the VERDICT that refuses to certify."""
    from app.services.agents.compliance.scoring import scoring_service

    findings = [{"severity": "high", "confidence": 0.9, "category": "claims"}]
    scores = scoring_service.calculate_scores([dict(f) for f in findings])
    warned = scoring_service.calculate_scores([dict(f) for f in findings])

    assert warned["overall"] == scores["overall"]
    assert warned["grade"] == scores["grade"]


# --------------------------------------------------------------------------
# End to end through the real preprocess node.
# --------------------------------------------------------------------------


class _Chunk:
    def __init__(self, text, index):
        self.id = uuid.uuid4()
        self.text = text
        self.chunk_index = index
        self.chunk_metadata = {}


class _ChunkQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_):
        return self

    def order_by(self, *_):
        return self

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self._rows = rows

    def query(self, _target):
        return _ChunkQuery(self._rows)


@pytest.fixture
def preprocess(monkeypatch, cards):
    from app.config import settings
    from app.services import fact_card_service as fcs
    from app.services import preprocessing_service
    from app.services.rag.indexers import chunks_indexer

    monkeypatch.setattr(settings, "product_grounding_enabled", True)
    monkeypatch.setattr(fcs, "get_fact_card_service", lambda: cards)

    async def _no_index(**_kwargs):
        return 0

    monkeypatch.setattr(chunks_indexer, "upsert_chunks_for_submission", _no_index)

    class _Chunker:
        def __init__(self, _db):
            pass

        async def preprocess_submission(self, _submission_id):
            return 1

    monkeypatch.setattr(preprocessing_service, "ContextEngineeringService", _Chunker)

    def _run(text, declared_product_line):
        token = GraphContext.set_db_session(_Db([_Chunk(text, 0)]))
        try:
            return asyncio.run(graph_nodes.preprocess_node({
                "submission_id": str(uuid.uuid4()),
                "metadata": {"declared_product_line": declared_product_line},
            }))
        finally:
            GraphContext.reset(token)

    return _run


def test_known_product_plus_ungrounded_rider_warns_and_grades(preprocess):
    """The real submission's shape, end to end."""
    out = preprocess(KNOWN_PLUS_UNGROUNDED_RIDER, "ulip")
    md = out["metadata"]

    assert md["product_unresolved"]["rider_uins_without_fact_cards"] == ["116N216V01"]
    assert "degraded" not in md
    codes = [w["code"] for w in md["analysis_warnings"]]
    assert "rider_uins_without_fact_cards" in codes
    assert ComplianceEngine.evaluate_persistability({
        "chunks": out["chunks"], "status": "completed",
        "metadata": _with_evidence(md),
    }) == (True, None)


def test_the_warning_carries_the_uin_and_where_it_occurs(preprocess):
    out = preprocess(KNOWN_PLUS_UNGROUNDED_RIDER, "ulip")
    warning = next(
        w for w in out["metadata"]["analysis_warnings"]
        if w["code"] == "rider_uins_without_fact_cards"
    )

    assert warning["detail"]["uins"] == ["116N216V01"]
    assert warning["detail"]["chunk_indexes"] == [0]


def test_a_clean_known_product_document_raises_no_product_warning(preprocess):
    out = preprocess(KNOWN_ONLY, "ulip")
    md = out["metadata"]

    assert "product_unresolved" not in md
    assert not [
        w for w in (md.get("analysis_warnings") or [])
        if w["code"].endswith("fact_cards") or w["code"] == "unknown_uins"
    ]


def test_an_unknown_uin_with_no_resolvable_scope_still_refuses(preprocess):
    """Nothing resolved, nothing declarable — the scope itself is unprovable."""
    out = preprocess(UNKNOWN_UIN_ONLY, None)
    md = out["metadata"]

    assert md["degraded"] == "product_unresolved"
    assert md["product_unresolved"]["submission_scope"]
    assert ComplianceEngine.evaluate_persistability({
        "chunks": out["chunks"], "status": "completed",
        "metadata": _with_evidence(md),
    }) == (False, "product_unresolved")


def test_an_unknown_uin_under_a_declared_scope_warns_and_grades(preprocess):
    """The declaration proves the envelope; the unknown product's own
    obligations are the bounded gap."""
    out = preprocess(UNKNOWN_UIN_ONLY, "ulip")
    md = out["metadata"]

    assert md["product_unresolved"]["unknown_uins"] == ["116N999V01"]
    assert "degraded" not in md
    assert "unknown_uins" in [w["code"] for w in md["analysis_warnings"]]
    assert ComplianceEngine.evaluate_persistability({
        "chunks": out["chunks"], "status": "completed",
        "metadata": _with_evidence(md),
    }) == (True, None)


def test_a_declared_scope_conflict_still_refuses(preprocess):
    out = preprocess(KNOWN_ONLY, "term")
    md = out["metadata"]

    assert md["degraded"] == "product_unresolved"
    assert "conflicts with detected scope" in md["product_unresolved"]["submission_scope"][0]


def test_a_generic_document_with_a_declared_line_is_unchanged(preprocess):
    out = preprocess("Insurance is the subject matter of solicitation.", "ulip")
    md = out["metadata"]

    assert md["product_match"] == []
    assert "degraded" not in md


def test_a_global_multi_product_document_is_unchanged(preprocess):
    doc = (
        "Bajaj Life Invest Protect Goal III (UIN: 116L205V01) is a unit linked plan.\n"
        "Bajaj Life Smart Secure ROP (UIN: 116L215V01) offers return of premium.\n"
    )
    out = preprocess(doc, "global")

    assert "degraded" not in out["metadata"]
    assert ComplianceEngine.evaluate_persistability({
        "chunks": out["chunks"], "status": "completed",
        "metadata": _with_evidence(out["metadata"]),
    }) == (True, None)
