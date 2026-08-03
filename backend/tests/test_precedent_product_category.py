"""precedent_ingestion._product_category: Par/Non-Par heuristic.

Non-Par text must not be misread as Par (substring trap: "participating" is
contained inside "non-participating"), and the emitted tags must round-trip
through applicability.normalize_category into the canonical par/non_par
segments -- the two taggers share one vocabulary by design.
"""
import pytest

from app.services.precedent_ingestion import _product_category
from app.services.rag.applicability import normalize_category


@pytest.mark.parametrize("text,expected", [
    ("This is a Non-Par plan from the product brochure", "Non-Par"),
    ("This is a non-participating traditional plan", "Non-Par"),
    ("Fund value under this participating policy structure", "Par"),
    ("A with-profit endowment plan", "Savings"),  # existing "endowment" hint still wins
    ("no product signal here", None),
])
def test_product_category_par_nonpar(text, expected):
    assert _product_category(text) == expected


def test_par_and_nonpar_normalize_to_canonical_segments():
    assert normalize_category(_product_category("participating policy bonus")) == "par"
    assert normalize_category(_product_category("non-par plan, no bonus")) == "non_par"
