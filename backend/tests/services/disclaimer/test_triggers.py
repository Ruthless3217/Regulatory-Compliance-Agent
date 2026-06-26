import asyncio
from pathlib import Path
from app.services.disclaimer.registry import DisclaimerRegistry
from app.services.disclaimer.triggers import (
    ProductContext, derive_product_context, deterministic_triggers,
    collapse_precedence, resolve_required,
)

REG = DisclaimerRegistry(Path(__file__).resolve().parents[3] / "data" / "disclaimers")


class FakeCards:
    def __init__(self, mapping): self._m = mapping
    def get(self, uin): return self._m.get(uin)


def test_product_context_ulip_from_descriptor():
    cards = FakeCards({"X1": {"regulatory_descriptor": "A Non-Participating, Linked, Individual Savings plan"}})
    ctx = derive_product_context([{"uin": "X1"}], cards)
    assert ctx.is_ulip is True and ctx.is_par is False and ctx.has_product is True


def test_product_context_par_and_non_linked():
    cards = FakeCards({"X2": {"regulatory_descriptor": "A Participating, Non-Linked Individual plan"}})
    ctx = derive_product_context([{"uin": "X2"}], cards)
    assert ctx.is_par is True and ctx.is_ulip is False


def test_no_product():
    ctx = derive_product_context([], FakeCards({}))
    assert ctx.has_product is False


def test_deterministic_fires_keyword_and_product():
    ctx = ProductContext(is_ulip=True, is_par=False, has_product=True)
    doc = "Invest in our ULIP and save tax under Section 80C with guaranteed additions."
    fired = deterministic_triggers(doc, ctx, REG)
    assert "ulip_risk" in fired
    assert "tax_123_80c" in fired
    assert "guaranteed" in fired
    assert "general_product" in fired  # has_product


def test_precedence_drops_generic_tax_when_specific_fires():
    fired = {"tax_123_80c": "kw", "tax_generic": "kw", "general_product": "p", "generic_non_product": "p"}
    kept = collapse_precedence(fired, REG)
    assert "tax_123_80c" in kept
    assert "tax_generic" not in kept          # specific tax wins
    assert "general_product" in kept
    assert "generic_non_product" not in kept  # product wins over non-product


def test_precedence_two_specific_tax_coexist():
    # 80C and 10(10D) are distinct obligations — both survive; only generic drops.
    fired = {"tax_123_80c": "kw", "tax_11_10_10d": "kw", "tax_generic": "kw"}
    kept = collapse_precedence(fired, REG)
    assert "tax_123_80c" in kept
    assert "tax_11_10_10d" in kept
    assert "tax_generic" not in kept


def test_precedence_combined_supersedes_components():
    # The combined 123-&-11 disclaimer makes the single-section ones redundant.
    fired = {"tax_123_and_11": "kw", "tax_123_80c": "kw",
             "tax_11_10_10d": "kw", "tax_generic": "kw"}
    kept = collapse_precedence(fired, REG)
    assert "tax_123_and_11" in kept
    assert "tax_123_80c" not in kept
    assert "tax_11_10_10d" not in kept
    assert "tax_generic" not in kept


def test_resolve_required_unions_llm_backstop():
    ctx = ProductContext(is_ulip=False, is_par=False, has_product=True)
    doc = "Our fund delivered strong double-digit growth last year."  # no 'past performance' literal

    async def fake_llm(_doc, _types):
        return ["past_performance"]

    required, degraded = asyncio.run(resolve_required(doc, ctx, REG, llm_call=fake_llm, enable_llm=True))
    assert "past_performance" in required
    assert required["past_performance"]["source"] == "llm"
    assert degraded is False


def test_resolve_required_degrades_on_llm_error():
    ctx = ProductContext(is_ulip=False, is_par=False, has_product=True)

    async def boom(_doc, _types):
        raise RuntimeError("llm down")

    required, degraded = asyncio.run(resolve_required("tax-free under Section 80C", ctx, REG, llm_call=boom, enable_llm=True))
    assert degraded is True
    assert "tax_123_80c" in required  # deterministic still fired
    assert required["tax_123_80c"]["source"] == "deterministic"


def test_resolve_required_combined_supersedes_deterministic_component():
    # Deterministic fires the 80C component (tax_123_80c via '80c'); the LLM
    # backstop surfaces the broader combined 123-&-11 disclaimer, whose verbatim
    # text covers BOTH Section 123/80C and Section 11/10(10D). The combined
    # supersedes the narrower component, so the checker verifies the combined
    # text (surfaced as source='llm'). This is the ONE case where an LLM
    # obligation replaces a deterministic one — only because it fully covers it.
    ctx = ProductContext(is_ulip=False, is_par=False, has_product=True)
    doc = "tax-free under Section 80C"

    async def fake_llm(_doc, _types):
        return ["tax_123_and_11"]  # llm_obligation_type of the combined disclaimer

    required, degraded = asyncio.run(
        resolve_required(doc, ctx, REG, llm_call=fake_llm, enable_llm=True)
    )
    assert "tax_123_and_11" in required
    assert required["tax_123_and_11"]["source"] == "llm"
    assert "tax_123_80c" not in required   # component superseded by the combined
    assert degraded is False
