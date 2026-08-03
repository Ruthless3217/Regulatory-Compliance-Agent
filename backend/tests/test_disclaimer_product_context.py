"""derive_product_context must read structural_flags, not regex the free-text
regulatory_descriptor.

Root cause (live bug, 2026-07-30): the old implementation regexed
regulatory_descriptor for a bare "linked"/"participating" word, excluding only
the HYPHENATED forms "non-linked"/"non-participating". Real fact-card
descriptors use a SPACE ("Non Linked", "Non Participating"), so the exclusion
never matched and every non-linked, non-participating product was misread as
both ULIP and PAR. Fixed to OR the structural is_unit_linked/is_participating
flags across every get_all(uin) variant card, mirroring
app.services.rag.applicability.build_scope's existing pattern.
"""
from pathlib import Path

from app.services.disclaimer.triggers import derive_product_context
from app.services.fact_card_service import FactCardService

CARDS_DIR = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"


def _real_cards():
    return FactCardService(CARDS_DIR)


# Confirmed-live misclassified products: all three are non-linked, non-par, but
# the old descriptor-regex flagged all three as is_ulip=True, is_par=True.
def test_fortune_gain_ii_is_ulip_not_par():
    # 116L196V04: "A Unit-linked Non Participating ... Plan" — genuinely ULIP,
    # genuinely non-par.
    ctx = derive_product_context([{"uin": "116L196V04"}], _real_cards())
    assert ctx.is_ulip is True
    assert ctx.is_par is False
    assert ctx.has_product is True


def test_goal_suraksha_is_neither_ulip_nor_par():
    # 116N155V19: "A Non linked, Non Participating, ... Savings Plan".
    ctx = derive_product_context([{"uin": "116N155V19"}], _real_cards())
    assert ctx.is_ulip is False
    assert ctx.is_par is False


def test_smart_protection_goal_is_neither_ulip_nor_par():
    # 116N174V05: "A Non Linked, Non Participating, ... Term Plan".
    ctx = derive_product_context([{"uin": "116N174V05"}], _real_cards())
    assert ctx.is_ulip is False
    assert ctx.is_par is False


def test_no_product_has_neither_flag():
    ctx = derive_product_context([], _real_cards())
    assert ctx.has_product is False
    assert ctx.is_ulip is False
    assert ctx.is_par is False


def test_ors_flags_across_multiple_resolved_products():
    # One non-ulip/non-par product plus one ulip product resolved together
    # must OR to is_ulip=True (still non-par).
    ctx = derive_product_context(
        [{"uin": "116N155V19"}, {"uin": "116L196V04"}], _real_cards()
    )
    assert ctx.is_ulip is True
    assert ctx.is_par is False


def test_falls_back_to_get_when_fact_cards_has_no_get_all():
    """Same get_all-preferred / get-fallback contract as build_scope."""

    class _GetOnly:
        def get(self, uin):
            return {"structural_flags": {"is_unit_linked": True, "is_participating": True}}

    ctx = derive_product_context([{"uin": "ANY"}], _GetOnly())
    assert ctx.is_ulip is True
    assert ctx.is_par is True
