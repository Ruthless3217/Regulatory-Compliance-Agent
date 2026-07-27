"""Semantic document-type awareness (Workstream A, 2026-07-15).

The pipeline previously had no concept of *what kind* of document it was grading
(only the file format), so a blog/article that merely named a product was asked
for that product's UIN. This module supplies:

  * the vocabulary of document types,
  * `requires_product_mandatory_elements` — the gate that decides whether the
    UIN / regulatory-descriptor "MUST STATE" obligations apply, and
  * `classify_document_type` — a cheap LLM pre-classifier whose result the
    submission form pre-fills and the user confirms (hybrid).

Fail-closed: an unknown / unset type is treated as a product document (strict),
so nothing is silently under-checked. Only an explicit editorial type relaxes
the mandatory-element obligations.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Product-marketing collateral (brochures, ads, leaflets, product landing pages)
# must carry the UIN + regulatory descriptor. Editorial content must not be asked
# for them.
PRODUCT_DOCUMENT_TYPES = {"product_marketing"}

KNOWN_DOCUMENT_TYPES = {
    "product_marketing",  # brochure / ad / leaflet / product landing page
    "blog_article",       # blog post, article, editorial
    "social",             # social media post
    "email",              # email / newsletter
    "website",            # corporate / editorial web page (non-product)
    "other",
}

# What the analysis gate defaults to when the type is missing/unknown.
DEFAULT_DOCUMENT_TYPE = "product_marketing"


def normalize_document_type(document_type: Optional[str]) -> Optional[str]:
    """Lowercase/trim and validate against the known vocabulary. Returns None for
    anything unrecognized (caller decides the fallback)."""
    if not document_type:
        return None
    dt = str(document_type).strip().lower()
    return dt if dt in KNOWN_DOCUMENT_TYPES else None


def requires_product_mandatory_elements(document_type: Optional[str]) -> bool:
    """True iff this document must carry the product's mandatory elements (UIN,
    regulatory descriptor). Strict by default: unknown/unset → True; only an
    explicit non-product type (blog/social/email/website/other) relaxes it."""
    dt = normalize_document_type(document_type)
    if dt is None:
        return True  # fail-closed
    return dt in PRODUCT_DOCUMENT_TYPES


async def classify_document_type(text: str) -> str:
    """LLM pre-classifier: map the content to one KNOWN_DOCUMENT_TYPES value.

    Uses the cheap critic profile. Returns a valid type; on any failure or an
    out-of-vocabulary answer, falls back to DEFAULT_DOCUMENT_TYPE (strict) so the
    gate never relaxes on a bad guess. This is a *suggestion* — the user confirms
    it in the form before analysis runs.
    """
    from pydantic import BaseModel, Field

    class _DocTypeResult(BaseModel):
        document_type: str = Field(..., description="One of the allowed types.")

    prompt = (
        "Classify the marketing/communication content below into exactly ONE of "
        "these document types:\n"
        "  product_marketing — a product brochure, advertisement, leaflet, or "
        "product landing page selling a specific insurance product.\n"
        "  blog_article — an educational blog post, article, or editorial (tips, "
        "explainers, 'how to choose…') not selling one specific product.\n"
        "  social — a short social-media post.\n"
        "  email — an email or newsletter.\n"
        "  website — a corporate/editorial web page that is not a product page.\n"
        "  other — none of the above.\n\n"
        f"CONTENT:\n{(text or '')[:6000]}\n\n"
        "Return only the single best-matching document_type."
    )
    try:
        from app.services.llm_service import critic_llm_service

        result = await critic_llm_service.generate_structured_response(
            prompt=prompt,
            output_model=_DocTypeResult,
            tool_name="document_type_classifier",
            temperature=0.0,
        )
        dt = normalize_document_type(getattr(result, "document_type", None))
        if dt is None:
            logger.info(
                "document_type classifier returned unrecognized %r; defaulting to %s",
                getattr(result, "document_type", None), DEFAULT_DOCUMENT_TYPE,
            )
            return DEFAULT_DOCUMENT_TYPE
        return dt
    except Exception as e:  # pragma: no cover - defensive; fail strict
        logger.warning("document_type classification failed (%s); defaulting strict", e)
        return DEFAULT_DOCUMENT_TYPE
