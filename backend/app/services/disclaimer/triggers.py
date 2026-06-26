"""Decide which disclaimers a document is obligated to carry.

Hybrid: a deterministic layer (product line + keyword regex) unioned with an
optional LLM backstop that recovers paraphrased obligations the regex misses.
The LLM only ADDS obligations; it never removes a deterministic one. If it
fails, we fall back to deterministic-only and flag recall as degraded.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .registry import Disclaimer, DisclaimerRegistry

logger = logging.getLogger(__name__)

# Disclaimer ids that belong to a mutually-exclusive family. Within a family,
# collapse_precedence keeps only the highest-precedence fired member.
_TAX_FAMILY = {"tax_123_80c", "tax_11_10_10d", "tax_123_and_11", "tax_generic"}
_PRODUCT_FAMILY = {"general_product", "generic_non_product"}


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
    """Within each mutually-exclusive family keep only the highest-precedence
    fired member; pass everything else through unchanged."""
    def _winner(ids: List[str]) -> Optional[str]:
        present = [i for i in ids if i in fired]
        if not present:
            return None
        return max(present, key=lambda i: (registry.get(i).precedence if registry.get(i) else 0))

    out = dict(fired)
    for family in (_TAX_FAMILY, _PRODUCT_FAMILY):
        win = _winner(list(family))
        if win is None:
            continue
        for i in family:
            if i != win and i in out:
                del out[i]
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
        # Union, then re-collapse families so an LLM-only specific tax beats the generic.
        merged = dict(det)
        for did in llm_fired:
            merged.setdefault(did, "llm_backstop")
        merged = collapse_precedence(merged, registry)
        out: Dict[str, Dict[str, str]] = {}
        for did in merged:
            if did in required:
                out[did] = required[did]
            else:
                out[did] = {"provenance": f"llm:{llm_fired.get(did, 'obligation')}", "source": "llm"}
        required = out

    return required, recall_degraded
