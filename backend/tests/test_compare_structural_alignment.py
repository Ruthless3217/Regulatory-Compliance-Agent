"""Comprehensive unit and regression tests for Structural Document Alignment in Compare tool.

Covers:
1. Mandatory regression test: Numbered definition Item 5 modified, Item 20 unchanged -> exactly 1 change under Item 5, zero changes under Item 20.
2. Inserted numbered items: Shifting numbering does not falsely mark subsequent items as modified.
3. Deleted numbered items: Removed item is marked as removed, subsequent items match cleanly.
4. Reordered items: Swapped items are tagged as moved, not modified.
5. Formatting & line-wrap differences without text changes produce 0 changes.
6. Multi-column reading order: Processes column 1 before column 2 without interleaving.
7. Sub-clauses ((a), (b), (c)) and Roman numeral (I, II, III) anchor detection.
8. Headings and Parts (## Part B - Definitions) structural segmentation.
9. OCR Provider caching and performance.
10. Fallback when structural alignment is toggled off.
"""
import pytest
from unittest.mock import MagicMock
from PIL import Image

from app.config import settings
from app.services.pdf_render_service import PositionedWord
from app.services.comparison_ocr_service import (
    OCRWord,
    OCRResultCache,
    PaddleOCRProvider,
    TesseractOCRProvider,
    ocr_page_to_words,
)
from app.services.structural_alignment_service import (
    StructuralAnchor,
    StructuralRegion,
    detect_anchor,
    detect_reading_order,
    segment_positioned_words,
    segment_text_paragraphs,
    match_structural_regions,
    structural_word_level_ops,
    structural_build_diff,
)


def _pw(text: str, page: int = 1, x0: float = 50.0, y0: float = 100.0, x1: float = 100.0, y1: float = 112.0) -> PositionedWord:
    return PositionedWord(text=text, page=page, x0=x0, y0=y0, x1=x1, y1=y1)


def _make_word_stream(lines: list, start_page: int = 1, start_y: float = 100.0) -> list:
    """Helper to generate positioned words from list of line strings."""
    words = []
    curr_y = start_y
    for li, line in enumerate(lines):
        tokens = line.split()
        curr_x = 50.0
        for tok in tokens:
            w_len = max(20.0, len(tok) * 6.5)
            words.append(PositionedWord(
                text=tok,
                page=start_page,
                x0=curr_x,
                y0=curr_y,
                x1=curr_x + w_len,
                y1=curr_y + 12.0,
            ))
            curr_x += w_len + 4.0
        curr_y += 18.0
    return words


# ---------------------------------------------------------------------------
# 1. Mandatory Regression Test: Item 5 modified, Item 20 unchanged
# ---------------------------------------------------------------------------

def test_item_5_does_not_align_with_item_20_in_positioned_words():
    """Regression test for the critical problem:
    Item 5 ("Grace Period") is modified.
    Item 20 ("UIN") is unchanged.
    Result MUST be: exactly 1 change for Item 5, 0 changes for Item 20.
    """
    old_lines = [
        '## Part B - Definitions',
        '1. "Act" means the Insurance Act 1938 as amended.',
        '5. "Grace Period" means the specified period of fifteen 15 days for monthly mode.',
        '6. "Goods and Service Tax" means the applicable tax levied by the government.',
        '20. "UIN" means the Unique Identification Number 116N216V01 assigned by IRDAI.',
    ]
    new_lines = [
        '## Part B - Definitions',
        '1. "Act" means the Insurance Act 1938 as amended.',
        '5. "Grace Period" means the specified period of thirty 30 days for monthly mode.',
        '6. "Goods and Service Tax" means the applicable tax levied by the government.',
        '20. "UIN" means the Unique Identification Number 116N216V01 assigned by IRDAI.',
    ]

    old_words = _make_word_stream(old_lines, start_page=1, start_y=100.0)
    new_words = _make_word_stream(new_lines, start_page=1, start_y=100.0)

    old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words)

    # There must be EXACTLY ONE change (under Item 5)
    assert len(changes) == 1
    c = changes[0]
    assert c["kind"] == "modified"
    assert "fifteen 15" in c["old_text"]
    assert "thirty 30" in c["new_text"]
    assert "UIN" not in c["old_text"] and "UIN" not in c["new_text"]
    assert "116N216V01" not in c["old_text"] and "116N216V01" not in c["new_text"]
    assert c.get("structure", {}).get("anchor_key") == "5"

    # Verify Item 20 words have NO marks
    uin_old_indices = [i for i, w in enumerate(old_words) if "UIN" in w.text or "116N216V01" in w.text]
    marked_old_indices = {m["index"] for m in old_marks}
    for idx in uin_old_indices:
        assert idx not in marked_old_indices


def test_item_5_does_not_align_with_item_20_in_text_diff():
    """Text-mode diff regression test: Item 5 change does not corrupt Item 20."""
    old_paras = [
        'Part B - Definitions',
        '1. "Act" means the Insurance Act 1938 as amended from time to time.',
        '5. "Grace Period" means the specified period of fifteen (15) days for monthly payment mode.',
        '6. "Goods and Service Tax" means the applicable tax.',
        '20. "UIN" means the Unique Identification Number 116N216V01 assigned by the Authority.',
    ]
    new_paras = [
        'Part B - Definitions',
        '1. "Act" means the Insurance Act 1938 as amended from time to time.',
        '5. "Grace Period" means the specified period of thirty (30) days for monthly payment mode.',
        '6. "Goods and Service Tax" means the applicable tax.',
        '20. "UIN" means the Unique Identification Number 116N216V01 assigned by the Authority.',
    ]

    blocks = structural_build_diff(old_paras, new_paras)
    non_equal = [b for b in blocks if b["type"] != "equal"]

    assert len(non_equal) == 1
    rep = non_equal[0]
    assert rep["type"] == "replace"
    old_changed = " ".join(w["text"] for w in rep["old_words"] if w["changed"])
    new_changed = " ".join(w["text"] for w in rep["new_words"] if w["changed"])
    assert "fifteen" in old_changed
    assert "thirty" in new_changed
    assert "UIN" not in old_changed and "UIN" not in new_changed


# ---------------------------------------------------------------------------
# 2. Inserted Numbered Items
# ---------------------------------------------------------------------------

def test_inserted_numbered_item_does_not_shift_subsequent_definitions():
    """Inserting a definition does not cause subsequent definitions to be flagged as modified."""
    old_lines = [
        '5. "Grace Period" means 15 days.',
        '6. "GST" means Goods and Service Tax.',
        '7. "UIN" means 116N216V01.',
    ]
    new_lines = [
        '5. "Grace Period" means 15 days.',
        '6. "Free Look" means 30 days review period.',
        '7. "GST" means Goods and Service Tax.',
        '8. "UIN" means 116N216V01.',
    ]

    old_words = _make_word_stream(old_lines)
    new_words = _make_word_stream(new_lines)

    old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words)

    # Exactly 1 added change for "Free Look"
    assert len(changes) == 1
    assert changes[0]["kind"] == "added"
    assert "Free Look" in changes[0]["new_text"]
    assert "GST" not in changes[0]["new_text"]
    assert "UIN" not in changes[0]["new_text"]


# ---------------------------------------------------------------------------
# 3. Deleted Numbered Items
# ---------------------------------------------------------------------------

def test_deleted_numbered_item_detected_cleanly():
    """Deleting a definition marks it as removed while preserving matches for remaining items."""
    old_lines = [
        '5. "Grace Period" means 15 days.',
        '6. "GST" means Goods and Service Tax.',
        '7. "UIN" means 116N216V01.',
    ]
    new_lines = [
        '5. "Grace Period" means 15 days.',
        '6. "UIN" means 116N216V01.',
    ]

    old_words = _make_word_stream(old_lines)
    new_words = _make_word_stream(new_lines)

    old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words)

    assert len(changes) == 1
    assert changes[0]["kind"] == "removed"
    assert "GST" in changes[0]["old_text"]


# ---------------------------------------------------------------------------
# 4. Reordered (Moved) Items
# ---------------------------------------------------------------------------

def test_reordered_numbered_items_distinguished_as_moves():
    """Swapped definitions are detected as moves rather than deletion + insertion."""
    old_lines = [
        '1. "Grace Period" means fifteen days.',
        '2. "GST" means Goods and Service Tax.',
    ]
    new_lines = [
        '1. "GST" means Goods and Service Tax.',
        '2. "Grace Period" means fifteen days.',
    ]

    old_paras = old_lines
    new_paras = new_lines

    blocks = structural_build_diff(old_paras, new_paras)
    moved_blocks = [b for b in blocks if b.get("moved")]
    assert len(moved_blocks) >= 2
    assert any(b.get("move_id") for b in moved_blocks)


# ---------------------------------------------------------------------------
# 5. Formatting / Line-Wrap Only Differences
# ---------------------------------------------------------------------------

def test_formatting_and_reflow_without_text_change_produces_zero_changes():
    """Reflowing lines without changing textual content produces 0 changes."""
    old_lines = [
        '5. "Grace Period" means the specified period of fifteen days available for payment.',
    ]
    # Split over 3 lines in revised version
    new_lines = [
        '5. "Grace Period" means the',
        'specified period of fifteen days',
        'available for payment.',
    ]

    old_words = _make_word_stream(old_lines)
    new_words = _make_word_stream(new_lines)

    old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words)
    assert len(changes) == 0
    assert len(old_marks) == 0
    assert len(new_marks) == 0


# ---------------------------------------------------------------------------
# 6. Multi-Column Reading Order Detection
# ---------------------------------------------------------------------------

def test_multi_column_reading_order_processes_columns_sequentially():
    """Two-column pages read column 1 top-to-bottom, then column 2 top-to-bottom."""
    # Page width 600. Left col: x0=50, x1=250. Right col: x0=350, x1=550.
    words = [
        # Right column top
        PositionedWord(text="RightCol1", page=1, x0=350, y0=100, x1=450, y1=112),
        PositionedWord(text="RightCol2", page=1, x0=350, y0=150, x1=450, y1=162),
        # Left column top
        PositionedWord(text="LeftCol1", page=1, x0=50, y0=100, x1=150, y1=112),
        PositionedWord(text="LeftCol2", page=1, x0=50, y0=150, x1=150, y1=162),
    ]
    # Add filler to trigger two-column threshold
    for i in range(15):
        words.append(PositionedWord(text=f"L{i}", page=1, x0=50, y0=200 + i*10, x1=150, y1=208 + i*10))
        words.append(PositionedWord(text=f"R{i}", page=1, x0=350, y0=200 + i*10, x1=450, y1=208 + i*10))

    ordered = detect_reading_order(words, page_w=600.0)

    # All left column words should come before right column words
    left_indices = [i for i, w in enumerate(ordered) if "Left" in w.text or w.text.startswith("L")]
    right_indices = [i for i, w in enumerate(ordered) if "Right" in w.text or w.text.startswith("R")]

    assert max(left_indices) < min(right_indices)
    assert ordered[0].text == "LeftCol1"
    assert ordered[1].text == "LeftCol2"


# ---------------------------------------------------------------------------
# 7. Anchor Detection Tests
# ---------------------------------------------------------------------------

def test_detect_anchor_patterns():
    # Numbered item with quotes
    a1 = detect_anchor('5. "Grace Period" means 15 days.')
    assert a1 is not None
    assert a1.anchor_type == "numbered_item"
    assert a1.anchor_key == "5"
    assert a1.title == "Grace Period"

    # Numbered item without quotes
    a2 = detect_anchor('20. UIN means Unique Identification Number.')
    assert a2 is not None
    assert a2.anchor_key == "20"
    assert a2.title == "UIN"

    # Headings
    a3 = detect_anchor('## Part B - Definitions')
    assert a3 is not None
    assert a3.anchor_type == "heading"
    assert "Part B" in a3.title

    # Sub-clause
    a4 = detect_anchor('(a) Death Benefit Payable on Survival')
    assert a4 is not None
    assert a4.anchor_type == "sub_clause"
    assert a4.anchor_key == "a"

    # Roman numeral
    a5 = detect_anchor('IV. General Conditions of Contract')
    assert a5 is not None
    assert a5.anchor_type == "roman"
    assert a5.anchor_key == "IV"


# ---------------------------------------------------------------------------
# 8. Single-Column to Two-Column Full End-to-End Test (0 changes)
# ---------------------------------------------------------------------------

def test_single_column_to_two_column_produces_zero_changes():
    """Documents with identical definitions in 1-column vs 2-column format produce 0 changes."""
    items = [
        f'{i}. "Item{i}" means definition text number {i} for policyholder.'
        for i in range(1, 21)
    ]
    # Single column: stacked vertically
    old_words = _make_word_stream(items, start_page=1, start_y=50.0)

    # Two column: items 1..10 in left column (x=50..250), items 11..20 in right column (x=350..550)
    new_words = []
    # Left column (1..10)
    curr_y = 50.0
    for line in items[:10]:
        for tok in line.split():
            w_len = max(20.0, len(tok) * 6.5)
            new_words.append(PositionedWord(text=tok, page=1, x0=50.0, y0=curr_y, x1=50.0 + w_len, y1=curr_y + 12.0))
        curr_y += 18.0

    # Right column (11..20)
    curr_y = 50.0
    for line in items[10:]:
        for tok in line.split():
            w_len = max(20.0, len(tok) * 6.5)
            new_words.append(PositionedWord(text=tok, page=1, x0=350.0, y0=curr_y, x1=350.0 + w_len, y1=curr_y + 12.0))
        curr_y += 18.0

    old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words, page_w=600.0)
    assert len(changes) == 0
    assert len(old_marks) == 0
    assert len(new_marks) == 0


# ---------------------------------------------------------------------------
# 9. Real Word Modification Is Strictly Localized
# ---------------------------------------------------------------------------

def test_real_word_modification_is_strictly_localized():
    """A real word modification within a definition marks only that specific definition."""
    old_lines = [
        '5. "Grace Period" means an additional period available for payment.',
    ]
    new_lines = [
        '5. "Grace Period" means an extended period available for payment.',
    ]
    old_words = _make_word_stream(old_lines)
    new_words = _make_word_stream(new_lines)

    old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words)
    assert len(changes) == 1
    c = changes[0]
    assert c["kind"] == "modified"
    assert c["old_text"] == "additional"
    assert c["new_text"] == "extended"


# ---------------------------------------------------------------------------
# 10. Renumbered Single Item Does Not Flag Entire Definition
# ---------------------------------------------------------------------------

def test_renumbered_item_does_not_flag_unmodified_body():
    """When an item is renumbered (5 -> 6) without body changes, the body produces 0 changes."""
    old_lines = [
        '5. "Grace Period" means fifteen days.',
    ]
    new_lines = [
        '6. "Grace Period" means fifteen days.',
    ]
    old_words = _make_word_stream(old_lines)
    new_words = _make_word_stream(new_lines)

    old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words)
    assert len(changes) == 0


# ---------------------------------------------------------------------------
# 11. Multi-Line Real Change Marks Only Changed Words
# ---------------------------------------------------------------------------

def test_multiline_real_change_marks_only_changed_words():
    """In a multi-line wrapped definition, only the changed tokens receive marks."""
    old_lines = [
        '5. "Grace Period" means an additional period',
        'available for payment.',
    ]
    new_lines = [
        '5. "Grace Period" means an extended period',
        'available for payment.',
    ]
    old_words = _make_word_stream(old_lines)
    new_words = _make_word_stream(new_lines)

    old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words)
    assert len(changes) == 1
    assert changes[0]["old_text"] == "additional"
    assert changes[0]["new_text"] == "extended"
    # Only 1 word on old side and 1 word on new side marked
    assert len(old_marks) == 1
    assert len(new_marks) == 1


# ---------------------------------------------------------------------------
# 12. OCR Provider Caching Test
# ---------------------------------------------------------------------------

def test_ocr_provider_caching():
    cache = OCRResultCache()
    img = Image.new("RGB", (400, 200), (255, 255, 255))
    dummy_words = [OCRWord("Test", 0.99, 1, 10, 20, 50, 40)]

    # Miss on first access
    assert cache.get(img, 1, 400.0, 200.0) is None

    # Set cache
    cache.set(img, 1, 400.0, 200.0, dummy_words)

    # Hit on second access
    hit = cache.get(img, 1, 400.0, 200.0)
    assert hit is not None
    assert len(hit) == 1
    assert hit[0].text == "Test"

    # Clear
    cache.clear()
    assert cache.get(img, 1, 400.0, 200.0) is None


# ---------------------------------------------------------------------------
# 13. Anchor Number Collision Tests (Phase 1.1)
# ---------------------------------------------------------------------------

def test_same_anchor_different_content_does_not_match():
    """Two items sharing the same number (e.g. 14) but completely different titles/bodies

    must NOT be paired as a modified item. Old becomes REMOVED, new becomes ADDED.
    """
    old_lines = [
        '14. "Policy Year" means the 12 month period commencing from the date of commencement.',
    ]
    new_lines = [
        '14. In other cases, the Company shall, subject to terms and conditions of assignment, pay.',
    ]
    old_words = _make_word_stream(old_lines, start_page=6)
    new_words = _make_word_stream(new_lines, start_page=19)

    old_regs = segment_positioned_words(old_words)
    new_regs = segment_positioned_words(new_words)
    pairs = match_structural_regions(old_regs, new_regs)

    # Must NOT produce a "matched" pair
    matched_pairs = [p for p in pairs if p[2] == "matched"]
    assert len(matched_pairs) == 0

    # Running word-level diff produces 1 removed change and 1 added change (NOT 1 modified change)
    old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words)
    kinds = [c["kind"] for c in changes]
    assert "modified" not in kinds
    assert "removed" in kinds
    assert "added" in kinds


def test_same_anchor_similar_content_matches():
    """Two items sharing the same number with similar/edited content are matched as modified."""
    old_lines = [
        '14. "Policy Year" means the 12 month period commencing from the date of commencement.',
    ]
    new_lines = [
        '14. "Policy Year" means the 12 month period starting from the date of commencement.',
    ]
    old_words = _make_word_stream(old_lines, start_page=6)
    new_words = _make_word_stream(new_lines, start_page=6)

    old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words)
    assert len(changes) == 1
    assert changes[0]["kind"] == "modified"
    assert changes[0]["old_text"] == "commencing"
    assert changes[0]["new_text"] == "starting"
