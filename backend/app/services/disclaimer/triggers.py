"""Decide which disclaimers a document is obligated to carry.

Hybrid: a deterministic layer (product line + keyword regex) unioned with an
optional LLM backstop that recovers paraphrased obligations the regex misses.
The LLM only ADDS obligations; it never drops a DETERMINISTIC obligation except
via a sanctioned combined disclaimer (_SANCTIONED_COMBINED_SUPPRESSORS) whose
verbatim text genuinely covers the components it supersedes. If the LLM fails,
we fall back to deterministic-only and flag recall as degraded.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .registry import Disclaimer, DisclaimerRegistry

logger = logging.getLogger(__name__)

# When the KEY disclaimer fires, the listed disclaimers become redundant and are
# dropped. This encodes spec precedence WITHOUT numeric tie-breaking:
#   - a specific tax disclaimer suppresses the GENERIC tax fallback;
#   - the COMBINED 123-&-11 disclaimer (fires only when BOTH "Section 123" and
#     "Section 11" appear) supersedes its two single-section components;
#   - a resolved-product disclaimer suppresses the no-product generic.
# The two single-section tax disclaimers (80C / 10(10D)) are NOT mutually
# exclusive: a document claiming both obligations must carry both.
# (User decision 2026-06-26.)
_SUPPRESSES: Dict[str, set] = {
    "tax_123_80c": {"tax_generic"},
    "tax_11_10_10d": {"tax_generic"},
    "tax_123_and_11": {"tax_generic", "tax_123_80c", "tax_11_10_10d"},
    "general_product": {"generic_non_product"},
}

# A combined disclaimer whose verbatim text FULLY covers the obligations it
# suppresses — so it may supersede them even when only the LLM surfaced it.
# Everything else may NOT let an LLM-only obligation drop a deterministic one.
_SANCTIONED_COMBINED_SUPPRESSORS = {"tax_123_and_11"}


@dataclass(frozen=True)
class ProductContext:
    is_ulip: bool
    is_par: bool
    has_product: bool


def derive_product_context(product_match: List[Dict[str, Any]], fact_cards: Any) -> ProductContext:
    is_ulip = is_par = False
    has_product = bool(product_match)
    for m in product_match or []:
        card = fact_cards.get(m.get("uin")) if hasattr(fact_cards, "get") else None
        descriptor = str((card or {}).get("regulatory_descriptor") or "").lower()
        if "non-linked" not in descriptor and re.search(r"\blinked\b", descriptor):
            is_ulip = True
        if "non-participating" not in descriptor and re.search(r"\bparticipating\b", descriptor):
            is_par = True
    return ProductContext(is_ulip=is_ulip, is_par=is_par, has_product=has_product)


def _product_line_fires(line: str, ctx: ProductContext) -> bool:
    if line == "ulip":
        return ctx.is_ulip
    if line == "par":
        return ctx.is_par
    if line == "any_product":
        return ctx.has_product
    if line == "non_product":
        return not ctx.has_product
    return False


def deterministic_triggers(
    document_text: str, ctx: ProductContext, registry: DisclaimerRegistry
) -> Dict[str, str]:
    """Return {disclaimer_id: provenance} for product-line + keyword matches."""
    fired: Dict[str, str] = {}
    for d in registry.all():
        product_lines = d.triggers.get("product_lines") or []
        for line in product_lines:
            if _product_line_fires(line, ctx):
                fired[d.id] = f"product_line={line}"
                break
        if d.id in fired:
            continue
        for pat in (d.triggers.get("keywords_regex") or []):
            m = re.search(pat, document_text, re.IGNORECASE)
            if m:
                fired[d.id] = f"matched '{m.group(0)}'"
                break
    return fired


def collapse_precedence(fired: Dict[str, str], registry: DisclaimerRegistry) -> Dict[str, str]:
    """Drop disclaimers made redundant by a more-specific/authoritative fired
    sibling (see _SUPPRESSES). Order-independent and deterministic — no
    precedence-number tie-breaking. `registry` is retained for interface
    stability and to ignore ids it does not recognize."""
    out = dict(fired)
    for fired_id in list(fired):
        if registry.get(fired_id) is None:
            continue
        for suppressed in _SUPPRESSES.get(fired_id, ()):
            out.pop(suppressed, None)
    return out


async def resolve_required(
    document_text: str,
    ctx: ProductContext,
    registry: DisclaimerRegistry,
    *,
    llm_call: Optional[Callable[[str, List[str]], Awaitable[List[str]]]] = None,
    enable_llm: bool = True,
) -> Tuple[Dict[str, Dict[str, str]], bool]:
    """Returns ({disclaimer_id: {provenance, source}}, recall_degraded)."""
    det = collapse_precedence(deterministic_triggers(document_text, ctx, registry), registry)
    required: Dict[str, Dict[str, str]] = {
        did: {"provenance": prov, "source": "deterministic"} for did, prov in det.items()
    }

    recall_degraded = False
    if enable_llm and llm_call is not None:
        type_to_id = {
            d.triggers.get("llm_obligation_type"): d.id
            for d in registry.all()
            if d.triggers.get("llm_obligation_type")
        }
        try:
            fired_types = await llm_call(document_text, list(type_to_id.keys()))
        except Exception as e:
            logger.warning("Disclosure LLM backstop failed (%s); deterministic-only.", e)
            recall_degraded = True
            fired_types = []
        llm_fired = {type_to_id[t]: t for t in (fired_types or []) if t in type_to_id}
        # Union the LLM-only obligations onto the deterministic set, then apply
        # SOURCE-GATED suppression: a DETERMINISTIC obligation may be dropped only
        # by a deterministic-sourced suppressor, or by the sanctioned combined-tax
        # disclaimer whose verbatim text genuinely covers its components. An LLM-only
        # obligation may be dropped by anything. This prevents an LLM-only
        # general_product from suppressing a deterministic generic_non_product
        # (product-line presence is deterministic ground truth).
        source = {did: "deterministic" for did in det}
        for did in llm_fired:
            source.setdefault(did, "llm")

        survivors = set(source)
        for fired_id in list(source):
            if registry.get(fired_id) is None:
                continue
            suppressor_is_det = source[fired_id] == "deterministic"
            for suppressed in _SUPPRESSES.get(fired_id, ()):
                if suppressed not in survivors:
                    continue
                suppressed_is_det = source.get(suppressed) == "deterministic"
                if suppressed_is_det and not (
                    suppressor_is_det or fired_id in _SANCTIONED_COMBINED_SUPPRESSORS
                ):
                    continue  # LLM-only suppressor must not drop a deterministic obligation
                survivors.discard(suppressed)

        out: Dict[str, Dict[str, str]] = {}
        for did in survivors:
            if did in required:
                out[did] = required[did]
            else:
                out[did] = {"provenance": f"llm:{llm_fired.get(did, 'obligation')}", "source": "llm"}
        required = out

    return required, recall_degraded
