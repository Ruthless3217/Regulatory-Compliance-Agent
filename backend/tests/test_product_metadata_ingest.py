"""Guideline ingest must declare a product scope, and must never succeed quietly.

generate_rules_from_text has REQUIRED product_line since 2026-08-03; the script
never passed one, the ValueError was caught into result["errors"], and the run
logged "✓ 0 rules" and exited 0 for a month. These tests pin both halves: the
scope actually derived from the filename, and the refusal to report success
when a file yields zero rules alongside errors.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import MagicMock

import pytest

from scripts import ingest_guidelines as ig
from scripts.backfill_product_metadata import (
    infer_product_line_from_title,
    product_line_for_uin,
)


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("ulip_compliance_guidelines_part1.md", "ulip"),
        ("ULIP_Compliance_Guidelines_Part4.md", "ulip"),
        ("term_insurance_compliance_guidelines_part7.md", "term"),
        ("retirement_and_pension_compliance_guidelines_part2.md", "pension_annuity"),
        ("tax_and_gst_compliance_guidelines_part5.md", "global"),
        ("disclaimers.md", "global"),
        ("some_new_circular.md", "global"),
        ("", "global"),
    ],
)
def test_filename_maps_to_a_supported_product_line(filename, expected):
    assert ig._product_line_for(filename) == expected


def test_every_derived_product_line_is_accepted_by_the_service_boundary():
    from app.services.rule_generator_service import _normalize_product_line

    for _, product_line in ig.PRODUCT_LINE_BY_PREFIX:
        assert _normalize_product_line(product_line) == product_line
    assert _normalize_product_line(ig._product_line_for("unmatched.md")) == "global"


@pytest.mark.parametrize(
    "filename,expected",
    [
        # ULIP marketing compliance is IRDAI's, not SEBI's.
        ("ulip_compliance_guidelines_part1.md", "irdai"),
        ("term_insurance_compliance_guidelines_part1.md", "irdai"),
        ("retirement_and_pension_compliance_guidelines_part2.md", "irdai"),
        ("sebi_mutual_fund_ad_code.md", "sebi"),
        ("tax_and_gst_compliance_guidelines_part1.md", "regulatory"),
        ("disclaimers.md", "regulatory"),
    ],
)
def test_regulator_mapping(filename, expected):
    assert ig._regulator_for(filename) == expected


def _patch_generator(monkeypatch, result):
    """Stub the service singleton ingest_file imports, and its DB session."""
    calls = {}

    async def fake_generate(**kwargs):
        calls.update(kwargs)
        return result

    from app.services import rule_generator_service as svc

    monkeypatch.setattr(
        svc.rule_generator_service, "generate_rules_from_text", fake_generate
    )
    monkeypatch.setattr(ig, "SessionLocal", MagicMock(return_value=MagicMock()))
    return calls


def test_ingest_passes_the_derived_product_line_and_regulator(monkeypatch, tmp_path):
    rule_id = str(uuid.uuid4())
    calls = _patch_generator(
        monkeypatch,
        {"rules_created": 1, "rules_failed": 0, "source_passages_indexed": 3,
         "rules": [{"id": rule_id}], "errors": []},
    )
    path = tmp_path / "ulip_compliance_guidelines_part1.md"
    path.write_text("Every ULIP advertisement must disclose fund risk.", encoding="utf-8")

    out = asyncio.run(ig.ingest_file(path, skip_existing=False))

    assert calls["product_line"] == "ulip"
    assert calls["regulator"] == "irdai"
    assert calls["document_title"] == "Ulip Compliance Guidelines Part1"
    assert out["rules_created"] == 1


def test_zero_rules_with_errors_raises_instead_of_reporting_success(monkeypatch, tmp_path):
    _patch_generator(
        monkeypatch,
        {"rules_created": 0, "rules_failed": 0, "source_passages_indexed": 0,
         "rules": [], "errors": ["product_line must be an explicit supported scope or global"]},
    )
    path = tmp_path / "ulip_compliance_guidelines_part1.md"
    path.write_text("Every ULIP advertisement must disclose fund risk.", encoding="utf-8")

    with pytest.raises(RuntimeError, match="0 rules created"):
        asyncio.run(ig.ingest_file(path, skip_existing=False))


def test_zero_rules_without_errors_is_not_an_error(monkeypatch, tmp_path):
    """A background-only document legitimately yields nothing."""
    _patch_generator(
        monkeypatch,
        {"rules_created": 0, "rules_failed": 0, "source_passages_indexed": 2,
         "rules": [], "errors": []},
    )
    path = tmp_path / "tax_and_gst_compliance_guidelines_part1.md"
    path.write_text("This chapter explains the GST framework.", encoding="utf-8")

    assert asyncio.run(ig.ingest_file(path, skip_existing=False))["rules_created"] == 0


def test_a_failed_file_makes_the_whole_run_exit_non_zero(monkeypatch, tmp_path):
    _patch_generator(
        monkeypatch,
        {"rules_created": 0, "rules_failed": 0, "source_passages_indexed": 0,
         "rules": [], "errors": ["boom"]},
    )
    (tmp_path / "ulip_compliance_guidelines_part1.md").write_text("x" * 60, encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["ingest_guidelines", "--dir", str(tmp_path)])

    assert asyncio.run(ig.main()) == 1


def test_empty_directory_exits_non_zero(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["ingest_guidelines", "--dir", str(tmp_path)])
    assert asyncio.run(ig.main()) == 1


# --- backfill inference (pure) ---------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        # ingest_guidelines stores the title-cased filename stem.
        ("Ulip Compliance Guidelines Part1", "ulip"),
        ("Term Insurance Compliance Guidelines Part7", "term"),
        ("Retirement And Pension Compliance Guidelines Part2", "pension_annuity"),
        ("Tax And Gst Compliance Guidelines Part5", "global"),
        ("Disclaimers", "global"),
        # Raw filenames also resolve, so re-ingested titles keep working.
        ("ulip_compliance_guidelines_part1.md", "ulip"),
        # Unknown documents stay NULL for manual tagging — never assumed global.
        ("Q3 Marketing Circular", None),
        ("", None),
        (None, None),
    ],
)
def test_source_doc_title_inference(title, expected):
    assert infer_product_line_from_title(title) == expected


class _Cards:
    def __init__(self, by_uin):
        self._by_uin = by_uin

    def get(self, uin):
        return self._by_uin.get(uin)


def test_uin_resolves_to_its_fact_card_product_family():
    cards = _Cards({
        "116L214V01": {"product_category": "ulip"},
        "116A057V03": {"product_category": "rider"},
        "116N175V03": {"product_category": "pension"},   # alias → canonical
        "116X999V01": {"product_category": "mystery"},   # unmappable
        "116Y000V01": {},                                # card without category
    })
    assert product_line_for_uin("116L214V01", cards) == "ulip"
    assert product_line_for_uin("116A057V03", cards) == "rider"
    assert product_line_for_uin("116N175V03", cards) == "pension_annuity"
    assert product_line_for_uin("116X999V01", cards) is None
    assert product_line_for_uin("116Y000V01", cards) is None
    assert product_line_for_uin("not-a-uin", cards) is None
    assert product_line_for_uin(None, cards) is None


def test_every_real_fact_card_category_maps_to_a_supported_scope():
    """The shipped cards must all be scopeable, or the backfill is decorative."""
    import json
    from pathlib import Path

    from app.services.rule_generator_service import _normalize_product_line

    cards_dir = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
    unmappable = []
    for path in sorted(cards_dir.glob("*.json")):
        card = json.loads(path.read_text(encoding="utf-8"))
        product_line = product_line_for_uin(card.get("uin"), _Cards({card.get("uin"): card}))
        if product_line is None or _normalize_product_line(product_line) != product_line:
            unmappable.append(f"{path.name}: {card.get('product_category')!r}")
    assert not unmappable, "fact cards with unscopeable product_category:\n" + "\n".join(unmappable)
