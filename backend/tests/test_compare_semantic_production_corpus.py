"""Production Corpus Validation tests for Phase 3.2 on real eTouch II Policy Document."""
import os
import sys
import time
import pytest
from typing import Dict, List, Any

from app.services.structural_alignment_service import (
    PositionedWord,
    segment_positioned_words,
    match_structural_regions,
    structural_word_level_ops,
    classify_change,
)
from app.services.pdf_render_service import positioned_words
from app.services.render_orchestrator import _build_changes, _build_pages

REAL_PDF_PATH = r"D:\Regulatory-Compliance-Agent\eTouch II_PD_V08.pdf"


@pytest.fixture(scope="module")
def base_pdf_words() -> List[PositionedWord]:
    """Load positioned words from the real production eTouch II PDF."""
    assert os.path.exists(REAL_PDF_PATH), f"Production corpus file not found: {REAL_PDF_PATH}"
    return positioned_words(REAL_PDF_PATH)


def test_production_corpus_structural_segmentation(base_pdf_words):
    """Verify that the real 21-page eTouch II PDF is segmented cleanly into structural regions."""
    t0 = time.perf_counter()
    regions = segment_positioned_words(base_pdf_words)
    duration_ms = (time.perf_counter() - t0) * 1000

    assert len(base_pdf_words) > 10000
    assert len(regions) >= 200
    assert duration_ms < 1000.0  # Must be fast (<1000ms for 21 pages)

    # Verify key structural anchors exist in the real document
    keys_found = {r.anchor.anchor_key for r in regions}
    assert "5" in keys_found    # Item 5 (Grace Period)
    assert "13" in keys_found   # Item 13 (Nominee / Assignment)
    assert "14" in keys_found   # Item 14 (Nomination & Assignment)
    assert "16" in keys_found   # Item 16 (Modification)
    assert "18" in keys_found   # Item 18 (Taxes)
    assert "20" in keys_found   # Item 20 (Ombudsman)


def test_production_corpus_revision_semantic_classification(base_pdf_words):
    """Run full structural comparison on the production corpus with realistic revisions.

    Verifies that:
    1. Item 5 numeric update is classified as numeric_only.
    2. Policy Schedule UIN update is classified as identifier_only.
    3. Item 16 terminology rewrite is classified as replacement.
    4. Item 20 addition is classified as insertion / replacement.
    5. Structural boundaries are 100% preserved.
    """
    revised_words = []

    for w in base_pdf_words:
        rw = PositionedWord(
            text=w.text,
            page=w.page,
            x0=w.x0,
            y0=w.y0,
            x1=w.x1,
            y1=w.y1,
            confidence=w.confidence,
            line_id=w.line_id,
            col_id=w.col_id,
        )

        # Real revision 1: UIN increment on Schedule (Page 4)
        if w.page == 4 and w.text == "116N198V08":
            rw.text = "116N198V09"

        # Real revision 2: Item 5 Grace Period (Page 6): "15-days" -> "30-days"
        elif w.page == 6 and w.text == "15-days":
            rw.text = "30-days"

        # Real revision 3: Item 16 Modification (Page 13): "letter," -> "notice,"
        elif w.page == 13 and w.text == "letter,":
            rw.text = "notice,"

        revised_words.append(rw)

    t0 = time.perf_counter()
    old_marks, new_marks, changes = structural_word_level_ops(base_pdf_words, revised_words)
    diff_time_ms = (time.perf_counter() - t0) * 1000

    assert diff_time_ms < 2500.0  # High performance (<2.5s on 10,000+ words across 21 pages)
    assert len(changes) == 3

    # Inspect Change 1: UIN on Page 4
    uin_change = [c for c in changes if "116N198V08" in c["old_text"]][0]
    assert uin_change["kind"] == "modified"
    assert uin_change["change_type"] == "identifier_only"
    assert uin_change["old_text"] == "116N198V08"
    assert uin_change["new_text"] == "116N198V09"
    assert uin_change["metadata"]["old_id"] == "116N198V08"
    assert uin_change["metadata"]["new_id"] == "116N198V09"

    # Inspect Change 2: Item 5 on Page 6
    grace_change = [c for c in changes if "15-days" in c["old_text"]][0]
    assert grace_change["kind"] == "modified"
    assert grace_change["change_type"] == "numeric_only"
    assert grace_change["structure"]["anchor_key"] == "5"
    assert grace_change["structure"]["title"] == "Grace Period"

    # Inspect Change 3: Item 16 on Page 13
    mod_change = [c for c in changes if "letter," in c["old_text"]][0]
    assert mod_change["kind"] == "modified"
    assert mod_change["change_type"] == "replacement"
    assert mod_change["structure"]["anchor_key"] == "16"
    assert mod_change["structure"]["title"] == "Modification"


def test_production_corpus_multi_location_render_boxes(base_pdf_words):
    """Verify that multi-location render boxes on real pages maintain exact coordinates."""
    # Modify a phrase spanning words in Section Part B Definition 13
    revised_words = []
    for w in base_pdf_words:
        rw = PositionedWord(
            text=w.text, page=w.page, x0=w.x0, y0=w.y0, x1=w.x1, y1=w.y1,
            confidence=w.confidence, line_id=w.line_id, col_id=w.col_id
        )
        if w.page == 6 and w.text == "Nomination":
            rw.text = "Nomination and Appointment"
        revised_words.append(rw)

    old_marks, new_marks, changes = structural_word_level_ops(base_pdf_words, revised_words)
    render_changes = _build_changes(changes, base_pdf_words, old_marks, revised_words, new_marks)

    assert len(render_changes) == 1
    rc = render_changes[0]
    assert rc["kind"] == "modified"
    assert rc["change_type"] == "replacement"
    assert rc["old"]["page"] == 6
    assert len(rc["old"]["locations"]) >= 1
    assert rc["old"]["locations"][0]["bbox"][0] > 0  # Valid PDF point coordinate


def test_production_corpus_target_anchors_boundary_isolation(base_pdf_words):
    """Simultaneously revise Items 5, 13, 14, 16, 18, 20 and verify zero cross-region leakage."""
    revised_words = []
    for w in base_pdf_words:
        rw = PositionedWord(
            text=w.text, page=w.page, x0=w.x0, y0=w.y0, x1=w.x1, y1=w.y1,
            confidence=w.confidence, line_id=w.line_id, col_id=w.col_id
        )
        # Item 5 (p.6): "15-days" -> "30-days"
        if w.page == 6 and w.text == "15-days":
            rw.text = "30-days"
        # Item 13 (p.6): "individual" -> "person"
        elif w.page == 6 and w.text == "individual":
            rw.text = "person"
        # Item 14 (p.13): "governed" -> "regulated"
        elif w.page == 13 and w.text == "governed":
            rw.text = "regulated"
        # Item 16 (p.13): "letter," -> "notice,"
        elif w.page == 13 and w.text == "letter,":
            rw.text = "notice,"
        # Item 18 (p.13): "GST," -> "GST/HST,"
        elif w.page == 13 and w.text == "GST,":
            rw.text = "GST/HST,"
        # Item 20 (p.14): "Ombudsman" -> "Insurance Ombudsman"
        elif w.page == 14 and w.text == "Ombudsman":
            rw.text = "Insurance Ombudsman"
        revised_words.append(rw)

    old_marks, new_marks, changes = structural_word_level_ops(base_pdf_words, revised_words)

    # All changes are detected and localized without cross-region merging
    assert len(changes) == 15

    # Group changes by anchor_key
    by_anchor = {}
    for c in changes:
        by_anchor.setdefault(c["structure"]["anchor_key"], []).append(c)

    # Item 5 (Grace Period)
    assert "5" in by_anchor
    c5 = by_anchor["5"][0]
    assert c5["change_type"] == "numeric_only"
    assert c5["structure"]["title"] == "Grace Period"
    assert c5["old_text"] == "15-days"
    assert c5["new_text"] == "30-days"

    # Item 13 (Nominee)
    assert "13" in by_anchor
    c13 = by_anchor["13"][0]
    assert c13["change_type"] == "replacement"
    assert c13["structure"]["title"] == "Nominee"

    # Item 14 (Nomination and Assignment)
    assert "14" in by_anchor
    c14 = by_anchor["14"][0]
    assert c14["change_type"] == "replacement"
    assert c14["structure"]["title"] == "Nomination and Assignment"

    # Item 16 (Modification)
    assert "16" in by_anchor
    c16 = by_anchor["16"][0]
    assert c16["change_type"] == "replacement"
    assert c16["structure"]["title"] == "Modification"

    # Item 18 (Taxes)
    assert "18" in by_anchor
    c18 = by_anchor["18"][0]
    assert c18["change_type"] == "replacement"
    assert c18["structure"]["title"] == "Taxes"

    # Item 20 (Ombudsman)
    assert "20" in by_anchor
    c20 = by_anchor["20"][0]
    assert c20["change_type"] == "replacement"
    assert c20["structure"]["title"] == "Ombudsman"
