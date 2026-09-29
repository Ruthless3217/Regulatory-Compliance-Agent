"""Unit and integration tests for Phase 3 Semantic Diff Intelligence & Change Classification."""
import pytest
from app.services.structural_alignment_service import (
    PositionedWord,
    classify_change,
    structural_word_level_ops,
    _is_numeric_expression,
    _is_identifier_expression,
)
from app.services.render_orchestrator import _build_changes


def test_classify_numeric_changes():
    """Test numeric-only change classification across various representations."""
    # Plain integers
    c_type, meta = classify_change("15", "30", "modified")
    assert c_type == "numeric_only"
    assert meta["category"] == "numeric"
    assert meta["old_value"] == "15"
    assert meta["new_value"] == "30"

    # Days quantity
    c_type, _ = classify_change("15 days", "30 days", "modified")
    assert c_type == "numeric_only"

    # Currencies with commas
    c_type, _ = classify_change("₹5,00,000", "₹6,00,000", "modified")
    assert c_type == "numeric_only"

    c_type, _ = classify_change("Rs. 50000", "Rs. 75000", "modified")
    assert c_type == "numeric_only"

    c_type, _ = classify_change("$1,000.50", "$2,000.75", "modified")
    assert c_type == "numeric_only"

    # Percentages
    c_type, _ = classify_change("8.5%", "9.0%", "modified")
    assert c_type == "numeric_only"

    c_type, _ = classify_change("18%", "12%", "modified")
    assert c_type == "numeric_only"

    # Dates
    c_type, _ = classify_change("31/03/2025", "31/03/2026", "modified")
    assert c_type == "numeric_only"

    c_type, _ = classify_change("15-08-2024", "26-01-2025", "modified")
    assert c_type == "numeric_only"


def test_classify_identifier_changes():
    """Test alphanumeric identifier and UIN classification."""
    c_type, meta = classify_change("116N216V01", "116N216V02", "modified")
    assert c_type == "identifier_only"
    assert meta["category"] == "identifier"
    assert meta["old_id"] == "116N216V01"
    assert meta["new_id"] == "116N216V02"

    c_type, _ = classify_change("IRDAI/NL-GEN/2023", "IRDAI/NL-GEN/2024", "modified")
    assert c_type == "identifier_only"

    c_type, _ = classify_change("POL-12345", "POL-98765", "modified")
    assert c_type == "identifier_only"


def test_classify_punctuation_only_changes():
    """Test punctuation differences with identical textual content."""
    c_type, meta = classify_change("policyholder;", "policyholder,", "modified")
    assert c_type == "punctuation_only"
    assert meta["category"] == "punctuation"

    c_type, _ = classify_change('"Grace Period"', 'Grace Period', "modified")
    assert c_type == "punctuation_only"

    c_type, _ = classify_change("terms—and—conditions", "terms and conditions", "modified")
    assert c_type == "punctuation_only"


def test_classify_whitespace_only_changes():
    """Test whitespace and spacing differences."""
    c_type, meta = classify_change("Grace   Period", "Grace Period", "modified")
    assert c_type == "whitespace_only"
    assert meta["category"] == "whitespace"


def test_classify_reordered_changes():
    """Test word reordering and structural moves."""
    # Structural or block moves with kind="moved" are classified as reordered
    c_type, meta = classify_change("Full section text", "Full section text", "moved")
    assert c_type == "reordered"
    assert meta["reason"] == "structural_move"

    # In-place phrase rewrites with kind="modified" are substantive replacements
    c_type, meta = classify_change("shall immediately notify", "immediately shall notify", "modified")
    assert c_type == "replacement"
    assert meta["category"] == "substantive_text"


def test_classify_substantive_replacement():
    """Test substantive text rewrites and replacements."""
    c_type, meta = classify_change("receipt of claim", "intimation of event", "modified")
    assert c_type == "replacement"
    assert meta["category"] == "substantive_text"


def test_classify_insertion_and_deletion():
    """Test additions and removals."""
    c_type, meta = classify_change("obsolete clause text", "", "removed")
    assert c_type == "deletion"
    assert meta["category"] == "deletion"

    c_type, meta = classify_change("", "newly inserted requirement", "added")
    assert c_type == "insertion"
    assert meta["category"] == "insertion"


def test_structural_word_level_ops_attaches_classification_metadata():
    """Verify structural_word_level_ops produces classified changes with metadata."""
    old_words = [
        PositionedWord("5.", 1, 10, 10, 20, 20),
        PositionedWord('"Grace', 1, 25, 10, 60, 20),
        PositionedWord('Period"', 1, 65, 10, 100, 20),
        PositionedWord("means", 1, 105, 10, 140, 20),
        PositionedWord("15", 1, 145, 10, 160, 20),
        PositionedWord("days", 1, 165, 10, 190, 20),
        PositionedWord("for", 1, 195, 10, 210, 20),
        PositionedWord("payment.", 1, 215, 10, 260, 20),
    ]

    new_words = [
        PositionedWord("5.", 1, 10, 10, 20, 20),
        PositionedWord('"Grace', 1, 25, 10, 60, 20),
        PositionedWord('Period"', 1, 65, 10, 100, 20),
        PositionedWord("means", 1, 105, 10, 140, 20),
        PositionedWord("30", 1, 145, 10, 160, 20),
        PositionedWord("days", 1, 165, 10, 190, 20),
        PositionedWord("for", 1, 195, 10, 210, 20),
        PositionedWord("payment.", 1, 215, 10, 260, 20),
    ]

    old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words)

    assert len(changes) == 1
    c = changes[0]
    assert c["kind"] == "modified"
    assert c["old_text"] == "15"
    assert c["new_text"] == "30"
    assert c["change_type"] == "numeric_only"
    assert c["metadata"]["category"] == "numeric"
    assert c["structure"]["anchor_key"] == "5"
    assert c["structure"]["title"] == "Grace Period"


def test_render_orchestrator_build_changes_forwards_classification():
    """Verify _build_changes includes change_type, metadata, and structure in RenderChange."""
    old_words = [PositionedWord("116N216V01", 1, 50, 50, 120, 65)]
    new_words = [PositionedWord("116N216V02", 1, 50, 50, 120, 65)]

    old_marks = [{"index": 0, "type": "changed", "change_id": "r0"}]
    new_marks = [{"index": 0, "type": "changed", "change_id": "r0"}]

    changes = [{
        "id": "r0",
        "kind": "modified",
        "change_type": "identifier_only",
        "metadata": {"category": "identifier", "old_id": "116N216V01", "new_id": "116N216V02"},
        "structure": {"anchor_type": "numbered_item", "anchor_key": "20", "title": "UIN"},
        "old_text": "116N216V01",
        "new_text": "116N216V02",
    }]

    render_changes = _build_changes(changes, old_words, old_marks, new_words, new_marks)

    assert len(render_changes) == 1
    rc = render_changes[0]
    assert rc["id"] == "r0"
    assert rc["kind"] == "modified"
    assert rc["change_type"] == "identifier_only"
    assert rc["metadata"]["category"] == "identifier"
    assert rc["structure"]["title"] == "UIN"
    assert rc["old"]["text"] == "116N216V01"
    assert rc["new"]["text"] == "116N216V02"
    assert rc["old"]["locations"][0]["bbox"] == [50, 50, 120, 65]


def test_structural_boundaries_preserved_with_change_classification():
    """Verify Item 5 and Item 20 boundaries remain strict while changes are classified."""
    old_words = [
        # Item 5
        PositionedWord("5.", 1, 10, 10, 20, 20),
        PositionedWord('"Grace', 1, 25, 10, 60, 20),
        PositionedWord('Period"', 1, 65, 10, 100, 20),
        PositionedWord("means", 1, 105, 10, 140, 20),
        PositionedWord("15", 1, 145, 10, 160, 20),
        PositionedWord("days.", 1, 165, 10, 195, 20),
        # Item 20
        PositionedWord("20.", 1, 10, 50, 25, 60),
        PositionedWord('"UIN"', 1, 30, 50, 60, 60),
        PositionedWord("means", 1, 65, 50, 100, 60),
        PositionedWord("116N216V01.", 1, 105, 50, 175, 60),
    ]

    new_words = [
        # Item 5 (15 -> 30 days)
        PositionedWord("5.", 1, 10, 10, 20, 20),
        PositionedWord('"Grace', 1, 25, 10, 60, 20),
        PositionedWord('Period"', 1, 65, 10, 100, 20),
        PositionedWord("means", 1, 105, 10, 140, 20),
        PositionedWord("30", 1, 145, 10, 160, 20),
        PositionedWord("days.", 1, 165, 10, 195, 20),
        # Item 20 (116N216V01 -> 116N216V02)
        PositionedWord("20.", 1, 10, 50, 25, 60),
        PositionedWord('"UIN"', 1, 30, 50, 60, 60),
        PositionedWord("means", 1, 65, 50, 100, 60),
        PositionedWord("116N216V02.", 1, 105, 50, 175, 60),
    ]

    old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words)

    # Exactly 2 changes: one in Item 5 (numeric_only), one in Item 20 (identifier_only)
    assert len(changes) == 2
    c5 = [c for c in changes if c["structure"]["anchor_key"] == "5"][0]
    c20 = [c for c in changes if c["structure"]["anchor_key"] == "20"][0]

    assert c5["old_text"] == "15"
    assert c5["new_text"] == "30"
    assert c5["change_type"] == "numeric_only"

    assert c20["old_text"] == "116N216V01."
    assert c20["new_text"] == "116N216V02."
    assert c20["change_type"] == "identifier_only"


def test_smart_quotes_and_typographic_punctuation_classification():
    """Verify smart quotes and typographic characters are classified as punctuation_only."""
    c_type, _ = classify_change('“Free Look Period”', '"Free Look Period"', "modified")
    assert c_type == "punctuation_only"

    c_type, _ = classify_change('«Grace Period»', '"Grace Period"', "modified")
    assert c_type == "punctuation_only"

    c_type, _ = classify_change('policy—holder', 'policy - holder', "modified")
    assert c_type == "punctuation_only"
