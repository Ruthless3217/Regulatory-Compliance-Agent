"""Stray control bytes in extracted text must not cost a legitimate finding.

Word/PDF extraction leaks C0 control characters where a space belongs.
Production chunks on 2026-07-31 contained:

    'option to\x16withdraw\x16the\x16Fund\x16value'
    'The objective of the strategy is\x03to optimise'

That is not cosmetic. verify_evidence_grounding drops any finding whose
`current_text` is not literally present in its chunk — the defence against
fabricated evidence. An LLM quoting such a passage normalises the control byte
to a space, the literal comparison fails, and a CORRECT finding is discarded as
"fabricated". The log shows this happening repeatedly.
"""
from app.services.preprocessing_service import PreprocessingService as P


def test_the_exact_production_strings_become_matchable():
    raw = "Alternatively, the nominee will have an option to\x16withdraw\x16the\x16Fund\x16value"
    out = P._sanitize_extracted(raw)
    assert "\x16" not in out
    # What the model would quote must now be found literally in the chunk.
    assert "option to withdraw the Fund value" in out


def test_x03_case_from_production():
    raw = "The objective of the strategy is\x03to optimise risk and return"
    out = P._sanitize_extracted(raw)
    assert "strategy is to optimise" in out


def test_control_chars_become_space_not_deleted():
    # Deleting would weld words together and break matching a second way.
    assert P._sanitize_extracted("alpha\x16beta") == "alpha beta"
    assert P._sanitize_extracted("alpha\x16beta") != "alphabeta"


def test_real_whitespace_is_preserved():
    # Tab / newline / carriage return are genuine formatting; chunking and
    # paragraph splitting depend on them.
    raw = "line one\nline two\r\n\tindented\n\npara"
    assert P._sanitize_extracted(raw) == raw


def test_del_and_low_controls_stripped():
    for ch in ("\x00", "\x01", "\x08", "\x0b", "\x0c", "\x1f", "\x7f"):
        assert P._sanitize_extracted(f"a{ch}b") == "a b", ch


def test_ordinary_text_untouched():
    raw = "Bajaj Life Smart Wealth Goal VII — 4% p.a. (guaranteed)"
    assert P._sanitize_extracted(raw) == raw


def test_unicode_is_not_mangled():
    # Rupee sign, non-breaking space and em dash are meaningful in this corpus
    # and are NOT C0 controls — they must survive.
    raw = "₹1,00,000 per annum — as per IRDAI"
    assert P._sanitize_extracted(raw) == raw


def test_empty_and_none_safe():
    assert P._sanitize_extracted("") == ""
    assert P._sanitize_extracted(None) is None
