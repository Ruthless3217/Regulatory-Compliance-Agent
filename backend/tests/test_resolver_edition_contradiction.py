"""A fuzzy name match must not cross an edition the document explicitly names.

The resolver strips version tokens (II, III, IV, VI, 2, 8) before matching so
that "eTouch" still resolves to "eTouch II" — a deliberate recall rule. It was
direction-blind: it also let "Invest Protect Goal PLUS" resolve to the card for
"Invest Protect Goal III" (116L205V01), because {invest, protect, goal} is
locally present and partial_ratio scored 91.2. Measured on the real GEO Smart
Secure submission (8a3c2db4): all five mentions of the phrase are followed by
"plus", the candidate's own "iii" never appears, and 116L205V01's card — with
`must_state: Plan UIN 116L205V01` and a different regulatory descriptor — was
injected as authoritative ground truth for a product the document never names.

The contract now: exact UIN > exact alias > local name identity > fuzzy
similarity, and similarity never overrides contradictory identity evidence.
For a version-bearing candidate, every local mention is classified by what the
document puts in the EDITION SLOT — the token adjacent to the identity phrase
in the same punctuation-free run:

    confirmed     the candidate's own version token is there   ("Goal III")
    contradicted  a different name-like token is there         ("Goal Plus")
    neutral       nothing name-like is there                   ("Goal.", "Goal is")

One confirmation wins; otherwise one contradiction refuses; otherwise the
neutral case keeps the existing no-version recall. "Name-like" is derived from
the corpus — a token matching the version pattern, or a non-brand token that
appears in any curated product name — never from a hand-written word list.

Rejected candidates are not silently dropped: they surface as
`edition_conflicts` in unresolved_product_signals and become an
`edition_conflicts` warning on the run, so the reviewer sees that the document
names a product the corpus has no card for.
"""
import json
from pathlib import Path

import pytest

from app.config import settings
from app.services.fact_card_service import FactCardService
from app.services.product_resolver import (
    edition_conflicts,
    resolve_products,
    unresolved_product_signals,
)

CARDS_DIR = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
MIN_FUZZY = settings.kb_min_fuzzy_score
SP = Path("C:/Users/YASHRA~2.LAW/AppData/Local/Temp/claude/"
          "D--Regulatory-Compliance-Agent/df311c81-9904-46f7-bd6d-61dd029a6f56/scratchpad")

# The scattered-vocabulary long document from the locality suite — mirrors the
# real collateral's token distribution and names no product on its own.
SCATTER = (
    "Our smart approach helps you secure your family's future with a protection "
    "plan that returns value over the long term. The group of benefits below is "
    "guaranteed subject to policy terms. Wealth creation is a goal for every "
    "policyholder, and the shield of life cover protects that goal. Term options "
    "are flexible; the return of premium variant secures the maturity benefit. "
) * 20


@pytest.fixture(scope="module")
def cards():
    svc = FactCardService(CARDS_DIR)
    assert not svc.availability_issues, svc.availability_issues
    return svc


@pytest.fixture(scope="module")
def synthetic_cards(tmp_path_factory):
    """A corpus that knows Invest Protect Goal III but NOT Goal Plus — the
    state the real corpus was in before the Smart Secure card carried the
    alias. Keeps the contradiction rule itself under test."""
    root = tmp_path_factory.mktemp("corpus_no_alias")
    root.joinpath("product_segments.json").write_text(
        (CARDS_DIR.parent / "product_segments.json").read_text(encoding="utf-8"),
        encoding="utf-8")
    d = root / "product_fact_cards"
    d.mkdir()
    # The whole real corpus as it stood before 2026-09-18: no Secure Plus
    # card, and no "Invest Protect Goal Plus" alias on the Smart Secure card.
    # The full set matters — brand tokens and the name vocabulary the edition
    # check relies on are derived from every card name.
    for path in CARDS_DIR.glob("*.json"):
        if path.name.startswith("116N216V01"):
            continue
        card = json.loads(path.read_text(encoding="utf-8"))
        if card.get("uin") == "116L215V01":
            card.pop("marketing_aliases", None)
        d.joinpath(path.name).write_text(json.dumps(card), encoding="utf-8")
    svc = FactCardService(d)
    assert not svc.availability_issues, svc.availability_issues
    return svc


def _uins(text, cards):
    return {m["uin"] for m in resolve_products(text, cards, min_fuzzy_score=MIN_FUZZY)}


def _long(mention):
    return SCATTER + "\n\n" + mention + "\n\n" + SCATTER[:3000]


# --------------------------------------------------------------------------
# 1. The defect.
# --------------------------------------------------------------------------


def test_invest_protect_goal_plus_does_not_resolve_to_goal_iii(cards):
    text = ("Bajaj Life Invest Protect Goal Plus - Elite Variant - A Unit-Linked "
            "Non-Participating Individual Life Savings Insurance Plan is designed "
            "for long-term wealth creation.")

    assert "116L205V01" not in _uins(text, cards)


def test_the_real_disclaimer_line_does_not_resolve_goal_iii(cards):
    """Verbatim from the GEO document's Disclaimers block."""
    line = ("This advertisement is designed for combination of Benefits of two "
            "individual products named (1) Bajaj Life Invest Protect Goal Plus – "
            "Elite Variant - A Unit-Linked Non-Participating Individual Life Savings "
            "Insurance Plan (UIN:116L215V01). (2) Bajaj Life Secure Plus - Shield "
            "with ROP Variant – A Non-Participating, Non-Linked, Individual Health "
            "Plan (UIN:116N216V01).")
    resolved = _uins(line, cards)

    assert "116L215V01" in resolved, "the exact UIN still resolves"
    assert "116L205V01" not in resolved, "the fuzzy cross-edition match does not"


def test_a_rejected_candidate_is_reported_not_silently_dropped(synthetic_cards):
    """On a corpus that knows only Goal III, 'Goal Plus' is a real gap."""
    text = "Bajaj Life Invest Protect Goal Plus - Elite Variant is a ULIP."
    conflicts = edition_conflicts(text, synthetic_cards)

    assert [c["uin"] for c in conflicts] == ["116L205V01"]
    conflict = conflicts[0]
    assert conflict["candidate"] == "Bajaj Life Invest Protect Goal III"
    assert conflict["document_edition"] == "plus"
    assert conflict["candidate_edition"] == "iii"


def test_the_conflict_reaches_the_unresolved_signals(synthetic_cards):
    signals = unresolved_product_signals(
        "Bajaj Life Invest Protect Goal Plus - Elite Variant is a ULIP.", synthetic_cards
    )

    assert [c["uin"] for c in signals["edition_conflicts"]] == ["116L205V01"]


def test_a_conflict_the_corpus_explains_is_not_a_gap(cards):
    """The real corpus DOES know 'Invest Protect Goal Plus': the Smart Secure
    card (116L215V01) carries it as a marketing alias, because Smart Secure ROP
    is the combination of Invest Protect Goal Plus – Elite Variant and Secure
    Plus. The Goal III contradiction is still detected — and then explained by
    the alias that resolved — so there is nothing to warn the reviewer about."""
    text = "Bajaj Life Invest Protect Goal Plus - Elite Variant is a ULIP."

    assert [(m["uin"], m["method"]) for m in
            resolve_products(text, cards, min_fuzzy_score=MIN_FUZZY)] == [
        ("116L215V01", "alias_match")]
    assert edition_conflicts(text, cards) == []
    assert unresolved_product_signals(text, cards)["edition_conflicts"] == []


def test_an_unexplained_conflict_next_to_an_explained_one_is_still_reported(cards):
    text = ("Bajaj Life Invest Protect Goal Plus is a ULIP. "
            "Bajaj Life Fortune Gain III offers market-linked growth.")
    conflicts = edition_conflicts(text, cards)

    assert [(c["uin"], c["document_edition"]) for c in conflicts] == [("116L196V04", "iii")]


# --------------------------------------------------------------------------
# 2. The general rule — an explicit contradictory edition blocks the candidate.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mention,blocked", [
    ("Bajaj Life Fortune Gain III offers market-linked growth.", "116L196V04"),
    ("Bajaj Life Goal Assure Platinum is our flagship.", "116L204V01"),
    ("Bajaj Life LongLife Goal Plus secures your future.", "116L203V01"),
    ("Bajaj Life eTouch Plus is a term plan.", "116N198V07"),
    ("Bajaj Life Future Wealth Gain V is unit linked.", "116L202V01"),
])
def test_a_different_edition_in_the_slot_blocks_the_fuzzy_candidate(mention, blocked, cards):
    assert blocked not in _uins(_long(mention), cards)
    assert blocked in {c["uin"] for c in edition_conflicts(_long(mention), cards)}


def test_a_contradicting_prefix_blocks_even_when_the_suffix_confirms(cards):
    """Both slots of a mention are weighed. "Group Fortune Gain II" carries
    the candidate's own "II" after the phrase AND a corpus name token the
    candidate lacks before it — mixed evidence is not proof, so it fails
    closed rather than resolving on the half that agrees."""
    text = _long("Bajaj Life Group Fortune Gain II is our newest plan.")

    assert "116L196V04" not in _uins(text, cards)
    assert "116L196V04" in {c["uin"] for c in edition_conflicts(text, cards)}


def test_a_non_name_prefix_is_neutral(cards):
    """'super' appears in no curated product name, so by the corpus's own
    evidence it is not an edition marker — the mention resolves."""
    text = _long("Bajaj Life Super Fortune Gain II is our newest plan.")

    assert "116L196V04" in _uins(text, cards)


# --------------------------------------------------------------------------
# 3. Exact regulatory identity outranks everything.
# --------------------------------------------------------------------------


def test_an_exact_uin_still_resolves_regardless_of_name_similarity(cards):
    text = _long("Refer to plan UIN: 116L205V01 for details.")
    matches = resolve_products(text, cards, min_fuzzy_score=MIN_FUZZY)

    assert [(m["uin"], m["method"]) for m in matches] == [("116L205V01", "uin_regex")]


def test_an_exact_uin_is_never_reported_as_an_edition_conflict(cards):
    """The UIN claims the product outright; the name path is not consulted."""
    text = _long("Bajaj Life Invest Protect Goal Plus (UIN: 116L205V01).")

    assert "116L205V01" in _uins(text, cards)
    assert edition_conflicts(text, cards) == []


# --------------------------------------------------------------------------
# 4. Aliases are exact identity and untouched.
# --------------------------------------------------------------------------


def test_marketing_alias_still_resolves(cards):
    text = _long("The Health Management Services table lists the benefits.")

    assert "116N198V07" in _uins(text, cards)


def test_short_acronym_alias_still_resolves(cards):
    assert "116N198V07" in _uins("Benefits under HMS are included.", cards)


def test_an_exact_alias_still_resolves_when_the_name_is_contradicted(cards):
    """Found in pre-commit review: the contradiction `continue` sat inside the
    product loop and skipped the alias loop, so an exact alias could no
    longer resolve a product whose NAME the document contradicted. That
    inverted EXACT ALIAS > LOCAL NAME. The alias resolves; the name
    contradiction is still reported, because both are things the document
    said and the reviewer should see both."""
    text = _long("Bajaj Life eTouch Plus includes Health Management Services.")
    method_by_uin = {m["uin"]: m["method"] for m in
                     resolve_products(text, cards, min_fuzzy_score=MIN_FUZZY)}

    assert method_by_uin["116N198V07"] == "alias_match", "the alias, not the name"
    assert [c["uin"] for c in edition_conflicts(text, cards)] == ["116N198V07"]


# --------------------------------------------------------------------------
# 5. Legitimate matches with no contradictory evidence keep working.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mention,expected", [
    ("Bajaj Life eTouch II is a term plan.", "116N198V07"),             # confirmed
    ("Bajaj Life eTouch protects your family.", "116N198V07"),          # neutral verb
    ("Choose Bajaj Life eTouch. Our advisors will help.", "116N198V07"),# punctuation
    ("Bajaj Life Fortune Gain II (UIN pending) grows wealth.", "116L196V04"),
    ("Bajaj Life Invest Protect Goal III is a ULIP.", "116L205V01"),   # confirmed
    ("Presenting Bajaj Life Goal Assure IV, a plan for you.", "116L204V01"),
    ("Bajaj Life Smart Secure (ROP) plan pays back premiums.", "116L215V01"),  # no version
])
def test_a_match_without_contradictory_evidence_still_resolves(mention, expected, cards):
    assert expected in _uins(_long(mention), cards)


def test_one_confirming_mention_outweighs_a_contradicting_one(cards):
    """A document that names BOTH editions is a two-product document; the
    card that IS named must not be refused because its sibling is also named."""
    text = _long("Compare Bajaj Life Invest Protect Goal III with the newer "
                 "Bajaj Life Invest Protect Goal Plus.")

    assert "116L205V01" in _uins(text, cards)
    assert edition_conflicts(text, cards) == []


def test_recall_across_every_version_bearing_card_is_preserved(cards):
    """Every card whose name carries a version must still resolve from its
    full name AND from its version-stripped name — the existing recall rule."""
    from app.services.product_resolver import _VERSION_TOKEN_RE

    by_uin = {p["uin"]: p for p in cards.all_products()}
    misses = []
    for product in cards.all_products():
        name = product["product_name"]
        stripped = " ".join(t for t in name.split() if not _VERSION_TOKEN_RE.match(t))
        if stripped == name:
            continue
        # A superseded card's name resolves its successor (same product, newer
        # approved UIN); the old UIN is reachable only by citing it exactly.
        successor = product.get("superseded_by")
        expected = successor if successor in by_uin else product["uin"]
        for form in (name, stripped):
            if expected not in _uins(_long(form), cards):
                misses.append((product["uin"], form))
    assert misses == []


# --------------------------------------------------------------------------
# 6-10. Collisions, riders, distinct filings — unchanged.
# --------------------------------------------------------------------------


def test_116l214v01_remains_ambiguous(cards):
    entry = next(m for m in resolve_products("Product UIN: 116L214V01.", cards,
                                             min_fuzzy_score=MIN_FUZZY)
                 if m["uin"] == "116L214V01")
    assert entry["ambiguous"] is True and len(entry["candidates"]) == 3


def test_116l211v02_remains_ambiguous(cards):
    entry = next(m for m in resolve_products("Product UIN: 116L211V02.", cards,
                                             min_fuzzy_score=MIN_FUZZY)
                 if m["uin"] == "116L211V02")
    assert entry["ambiguous"] is True and len(entry["candidates"]) == 2


def test_116n198_filing_versions_remain_distinct(cards):
    assert _uins("Bajaj Life Superwoman Term (UIN: 116N198V05).", cards) == {"116N198V05"}
    assert _uins("Bajaj Life eTouch II (UIN: 116N198V07).", cards) == {"116N198V07"}


def test_116n216v01_is_now_a_grounded_product(cards):
    """Bajaj Life Secure Plus (116N216V01) got its own card on 2026-09-18 —
    the health component of Smart Secure ROP is no longer a knowledge gap."""
    text = "Shield with ROP Variant (UIN:116N216V01)."
    signals = unresolved_product_signals(text, cards)

    assert _uins(text, cards) == {"116N216V01"}
    assert signals["rider_uins_without_fact_cards"] == []
    assert signals["unknown_uins"] == []
    assert cards.get("116N216V01")["product_name"] == "Bajaj Life Secure Plus"


# --------------------------------------------------------------------------
# 11. Locality is intact — a scattered document still names nothing.
# --------------------------------------------------------------------------


def test_scattered_long_document_still_names_no_product(cards):
    assert _uins(SCATTER, cards) == set()
    assert edition_conflicts(SCATTER, cards) == []


# --------------------------------------------------------------------------
# 12. The real GEO Smart Secure submission, from the stored chunks.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def geo_text():
    path = SP / "smart_secure_chunks.json"
    if not path.exists():
        pytest.skip("real-corpus chunk export not present on this machine")
    chunks = json.loads(path.read_text(encoding="utf-8-sig").strip())
    return "\n".join(c["text"] for c in chunks)


def test_geo_smart_secure_resolves_no_fabricated_goal_iii(geo_text, cards):
    """Both UINs the leaflet prints, nothing fabricated. Since 2026-09-18 the
    health component (116N216V01, Secure Plus) has its own card."""
    matches = resolve_products(geo_text, cards, min_fuzzy_score=MIN_FUZZY)

    assert [(m["uin"], m["method"]) for m in matches] == [
        ("116L215V01", "uin_regex"), ("116N216V01", "uin_regex")]


def test_geo_smart_secure_reports_the_cross_edition_name(geo_text, synthetic_cards):
    """On the pre-alias corpus the leaflet's 'Invest Protect Goal Plus' is a
    detected, reported contradiction of the Goal III card."""
    signals = unresolved_product_signals(geo_text, synthetic_cards)

    assert [c["uin"] for c in signals["edition_conflicts"]] == ["116L205V01"]
    assert signals["edition_conflicts"][0]["document_edition"] == "plus"


def test_geo_smart_secure_is_fully_grounded_on_the_current_corpus(geo_text, cards):
    signals = unresolved_product_signals(geo_text, cards)

    assert signals["edition_conflicts"] == []
    assert signals["rider_uins_without_fact_cards"] == []
    assert signals["unknown_uins"] == []


def test_geo_smart_secure_grounds_only_the_proven_product(geo_text, cards, monkeypatch):
    """End to end through the grounding path: 116L205V01's card must not be
    among the product_facts the prompt would carry."""
    import asyncio

    from app.services import fact_card_service as fcs
    from app.services.agents.graph import nodes as graph_nodes
    from app.services.rag.retrievers import product_docs_retriever as pdr

    class _NoPassages:
        async def retrieve(self, **_kw):
            return []

    monkeypatch.setattr(settings, "product_grounding_enabled", True)
    monkeypatch.setattr(fcs, "get_fact_card_service", lambda: cards)
    monkeypatch.setattr(pdr, "get_product_docs_retriever", lambda: _NoPassages())

    matches = resolve_products(geo_text, cards, min_fuzzy_score=MIN_FUZZY)
    grounded = graph_nodes._select_grounded_products(
        matches, geo_text, cards, settings.product_match_max
    )
    facts, _ = asyncio.run(graph_nodes._resolve_product_grounding(
        {"metadata": {"product_match": matches, "product_grounding_uins": grounded}},
        [{"id": "c0", "text": geo_text[:2000]}],
    ))

    assert sorted(c["uin"] for c in facts) == ["116L215V01", "116N216V01"]
    assert "116L205V01" not in {c["uin"] for c in facts}


# --------------------------------------------------------------------------
# The refusal reaches the run as a warning — through the real preprocess node.
# --------------------------------------------------------------------------


def test_the_conflict_becomes_a_run_warning_not_a_refusal(synthetic_cards, monkeypatch):
    cards = synthetic_cards
    import asyncio
    import uuid

    from app.services import fact_card_service as fcs
    from app.services import preprocessing_service
    from app.services.agents.compliance.engine import ComplianceEngine
    from app.services.agents.graph import nodes as graph_nodes
    from app.services.agents.graph.context import GraphContext
    from app.services.rag.indexers import chunks_indexer

    class _Chunk:
        def __init__(self, text):
            self.id, self.text, self.chunk_index, self.chunk_metadata = uuid.uuid4(), text, 0, {}

    class _Q:
        def __init__(self, rows): self._rows = rows
        def filter(self, *_): return self
        def order_by(self, *_): return self
        def all(self): return self._rows

    class _Db:
        def __init__(self, rows): self._rows = rows
        def query(self, _t): return _Q(self._rows)

    async def _no_index(**_kw): return 0

    class _Chunker:
        def __init__(self, _db): pass
        async def preprocess_submission(self, _sid): return 1

    monkeypatch.setattr(settings, "product_grounding_enabled", True)
    monkeypatch.setattr(fcs, "get_fact_card_service", lambda: cards)
    monkeypatch.setattr(chunks_indexer, "upsert_chunks_for_submission", _no_index)
    monkeypatch.setattr(preprocessing_service, "ContextEngineeringService", _Chunker)

    text = ("Bajaj Life Smart Secure ROP (UIN: 116L215V01) is a ULIP. It combines "
            "Bajaj Life Invest Protect Goal Plus - Elite Variant with a health cover.")
    token = GraphContext.set_db_session(_Db([_Chunk(text)]))
    try:
        out = asyncio.run(graph_nodes.preprocess_node({
            "submission_id": str(uuid.uuid4()),
            "metadata": {"declared_product_line": "ulip"},
        }))
    finally:
        GraphContext.reset(token)
    md = out["metadata"]

    assert [m["uin"] for m in md["product_match"]] == ["116L215V01"]
    assert "degraded" not in md, "an edition conflict is a warning, never a refusal"
    warning = next(w for w in md["analysis_warnings"] if w["code"] == "edition_conflicts")
    assert warning["detail"]["candidates"] == [{
        "uin": "116L205V01", "candidate": "Bajaj Life Invest Protect Goal III",
        "document_edition": "plus", "candidate_edition": "iii",
    }]
    assert ComplianceEngine.evaluate_persistability({
        "chunks": out["chunks"], "status": "completed",
        "metadata": {**md, "grounded_evidence": {"rules": 0, "precedents": 0, "product_facts": 1}},
    }) == (True, None)


# --------------------------------------------------------------------------
# Version successors (2026-09-18): one product, two approved UINs.
# --------------------------------------------------------------------------


def test_a_superseded_card_resolves_only_by_its_exact_uin(cards):
    """Guaranteed Pension Goal II was re-approved as 116N187V11; the V09 card
    stays (old collateral still cites it) but is marked superseded_by. Naming
    the product resolves the CURRENT version only; the old UIN is reachable by
    citing it exactly."""
    by_name = resolve_products(
        "Bajaj Life Guaranteed Pension Goal II gives guaranteed income for life.",
        cards, min_fuzzy_score=MIN_FUZZY)
    assert [(m["uin"], m["method"]) for m in by_name] == [("116N187V11", "name_fuzzy")]

    old = resolve_products("Guaranteed Pension Goal II (UIN: 116N187V09).", cards,
                           min_fuzzy_score=MIN_FUZZY)
    assert [(m["uin"], m["method"]) for m in old] == [("116N187V09", "uin_regex")]


def test_a_uin_matched_product_is_not_also_resolved_under_its_sibling_version(cards):
    """The real retirement document: 'Guaranteed Pension Goal II ... (UIN:
    116N187V11)'. Before, the identical name on the V09 card produced a second
    match and the run grounded on two editions of one plan."""
    text = ("Bajaj Life Guaranteed Pension Goal II is A Non Linked Non Participating "
            "Immediate & Deferred Annuity Plan (UIN: 116N187V11).")
    matches = resolve_products(text, cards, min_fuzzy_score=MIN_FUZZY)

    assert [(m["uin"], m["method"]) for m in matches] == [("116N187V11", "uin_regex")]
    assert unresolved_product_signals(text, cards)["unknown_uins"] == []


def test_the_secure_plus_card_scopes_as_health_and_non_par(cards):
    from app.services.rag.applicability import build_scope

    scope = build_scope([{"uin": "116N216V01"}], cards)

    assert scope.categories == {"health", "non_par"}
