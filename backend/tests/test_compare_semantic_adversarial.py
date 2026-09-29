"""Adversarial test suite for Phase 3.1 Semantic Diff Classification Hardening."""
import pytest
from app.services.structural_alignment_service import (
    PositionedWord,
    classify_change,
    structural_word_level_ops,
    _is_numeric_expression,
    _is_identifier_expression,
)
from app.services.render_orchestrator import _build_changes

# 40-case Adversarial Matrix
ADVERSARIAL_CASES = [
    # A. Numeric-Only Changes (1-11)
    (1, "numeric", "30 days", "60 days", "modified", "numeric_only", "True numeric quantity change"),
    (2, "numeric", "8.5%", "9.0%", "modified", "numeric_only", "True percentage change"),
    (3, "numeric", "₹5,00,000", "₹6,00,000", "modified", "numeric_only", "True currency amount change"),
    (4, "numeric", "Policy term: 10 years", "Policy term: 15 years", "modified", "replacement", "Full sentence with numeric change (isolated numeric in ops)"),
    (5, "numeric_adversarial", "30 days", "30 years", "modified", "numeric_only", "Unit change: days to years"),
    (6, "numeric_adversarial", "8.5% premium", "8.5% discount", "modified", "replacement", "Noun change: premium to discount"),
    (7, "numeric_adversarial", "10 years", "100 years", "modified", "numeric_only", "Lexically numeric duration increase"),
    (8, "numeric_adversarial", "₹5 lakh", "₹5 crore", "modified", "numeric_only", "Unit magnitude change: lakh to crore"),
    (9, "numeric_adversarial", "5%", "5 percent", "modified", "replacement", "Symbol to prose representation change"),
    (10, "numeric_adversarial", "5.0%", "5%", "modified", "numeric_only", "Precision formatting change"),
    (11, "numeric_adversarial", "1,000", "1000", "modified", "numeric_only", "Thousands separator formatting difference"),

    # B. Identifier-Only Changes (12-18)
    (12, "identifier", "123ABC456V01", "123ABC456V02", "modified", "identifier_only", "UIN version increment"),
    (13, "identifier", "116L205V01", "116L215V01", "modified", "identifier_only", "UIN product code change"),
    (14, "identifier_adversarial", "ABC123", "XYZ456", "modified", "identifier_only", "Standalone product code change"),
    (15, "identifier_adversarial", "123ABC456V01", "123ABC456V02 for 30 days", "modified", "replacement", "Identifier plus added clause"),
    (16, "identifier_adversarial", "Product UIN 123ABC456V01", "Product UIN 123ABC456V02", "modified", "replacement", "Multi-word phrase containing UIN change"),
    (17, "identifier_adversarial", "UIN 123ABC456V01", "Product UIN 123ABC456V02", "modified", "replacement", "Added prefix 'Product' and UIN change"),
    (18, "identifier_adversarial", "policy", "police", "modified", "replacement", "Ordinary dictionary words must NOT be identifiers"),

    # C. Whitespace Adversarial Changes (19-26)
    (19, "whitespace", "30-days", "30- days", "modified", "punctuation_only", "Space insertion after hyphen in numeric quantity (hyphen punct normalization)"),
    (20, "whitespace", "hello world", "hello  world", "modified", "whitespace_only", "Multiple spaces"),
    (21, "whitespace_adversarial", "hello world", "helloworld", "modified", "replacement", "Merged tokens change lexical identity"),
    (22, "whitespace_adversarial", "not applicable", "notapplicable", "modified", "replacement", "Compounded words without space"),
    (23, "whitespace_adversarial", "policy holder", "policyholder", "modified", "replacement", "Single compound word vs two words"),
    (24, "whitespace_adversarial", "₹ 5,000", "₹5,000", "modified", "numeric_only", "Currency symbol spacing"),
    (25, "whitespace_adversarial", "10 years", "10years", "modified", "numeric_only", "Number fused with unit noun recognized as numeric duration"),
    (26, "whitespace_adversarial", "30 days", "30\n days", "modified", "whitespace_only", "Line-break whitespace"),

    # D. Punctuation Adversarial Changes (27-31)
    (27, "punctuation", "Yes.", "Yes,", "modified", "punctuation_only", "Period vs comma"),
    (28, "punctuation", "Terms and Conditions:", "Terms and Conditions", "modified", "punctuation_only", "Trailing colon removed"),
    (29, "punctuation_adversarial", "₹5,00,000", "₹5.00.000", "modified", "punctuation_only", "Period used as separator in currency amount"),
    (30, "punctuation_adversarial", "1,000", "1.000", "modified", "numeric_only", "Comma vs decimal point in number"),
    (31, "punctuation_adversarial", "shall not", "shall, not", "modified", "punctuation_only", "Comma insertion altering cadence"),

    # E. Reordering Adversarial Changes (32-37)
    (32, "reordering_adversarial", "The policyholder shall immediately notify the company.", "The company shall immediately notify the policyholder.", "modified", "replacement", "Subject/object role reversal (multiset equal is substantive rewrite)"),
    (33, "reordering_adversarial", "The company may approve the claim.", "The claim may be approved by the company.", "modified", "replacement", "Active to passive voice with added words"),
    (34, "reordering_adversarial", "Submit the form before payment.", "Submit payment before the form.", "modified", "replacement", "Condition order inversion (multiset equal is substantive rewrite)"),
    (35, "reordering_adversarial", "Benefits apply after 30 days.", "Benefits apply after 60 days.", "modified", "replacement", "Number changed inside phrase"),
    (36, "reordering_adversarial", "The policyholder must notify the insurer.", "The insurer must notify the policyholder.", "modified", "replacement", "Obligation reversal (multiset equal is substantive rewrite)"),
    (37, "reordering_adversarial", "Death benefit is payable to the nominee.", "Nominee is payable the death benefit.", "modified", "replacement", "Grammatical syntax inversion (multiset equal is substantive rewrite)"),

    # F. Insertion, Deletion, Replacement (38-40)
    (38, "insertion", "", "within 30 days", "added", "insertion", "Pure addition"),
    (39, "deletion", "obsolete requirement", "", "removed", "deletion", "Pure deletion"),
    (40, "replacement", "Premium is payable annually within 30 days.", "Premium is payable monthly within 60 days.", "modified", "replacement", "Multiple substantive changes"),
]


@pytest.mark.parametrize("case_id,category,old_t,new_t,kind,expected,note", ADVERSARIAL_CASES)
def test_individual_adversarial_case(case_id, category, old_t, new_t, kind, expected, note):
    actual_type, meta = classify_change(old_t, new_t, kind)
    assert actual_type == expected, f"Case #{case_id} [{category}] failed: '{old_t}' -> '{new_t}' expected '{expected}' but got '{actual_type}' (meta={meta})"


def test_real_pipeline_subject_object_reversal_is_replacement():
    """Verify that in the real pipeline (structural_word_level_ops), subject/object reversal
    is broken into localized word replacements rather than a false harmless reorder.
    """
    old_words = [
        PositionedWord("The", 1, 10, 10, 30, 20),
        PositionedWord("policyholder", 1, 35, 10, 110, 20),
        PositionedWord("shall", 1, 115, 10, 145, 20),
        PositionedWord("notify", 1, 150, 10, 185, 20),
        PositionedWord("the", 1, 190, 10, 210, 20),
        PositionedWord("company.", 1, 215, 10, 270, 20),
    ]
    new_words = [
        PositionedWord("The", 1, 10, 10, 30, 20),
        PositionedWord("company", 1, 35, 10, 85, 20),
        PositionedWord("shall", 1, 90, 10, 120, 20),
        PositionedWord("notify", 1, 125, 10, 160, 20),
        PositionedWord("the", 1, 165, 10, 185, 20),
        PositionedWord("policyholder.", 1, 190, 10, 265, 20),
    ]

    old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words)

    # In the actual pipeline:
    # 1. 'policyholder' -> 'company' is a modified replacement
    # 2. 'company.' -> 'policyholder.' is a modified replacement
    assert len(changes) == 2
    for c in changes:
        assert c["kind"] == "modified"
        assert c["change_type"] == "replacement"
