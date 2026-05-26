"""Pure-unit tests for knowledge-base ingestion parsing/classification/alignment."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.knowledge_base_ingestion import (
    parse_compliance_comments,
    classify_category,
    classify_severity,
    align_comment_to_chunk,
    _pair_final_chunk,
    ParsedComment,
)


def test_parse_single_comment_line():
    raw = "[Shailja Saklani/Pune HO/Legal Compliance and FPU/Life]: Add product disclaimer below. (Context: For instance, Bajaj Allianz Smart Protection Goal is a comprehensive term insura...)"
    out = parse_compliance_comments(raw, source_file="x.json")
    assert len(out) == 1
    c = out[0]
    assert c.reviewer == "Shailja Saklani/Pune HO/Legal Compliance and FPU/Life"
    assert c.comment == "Add product disclaimer below."
    assert c.anchor.startswith("For instance, Bajaj Allianz Smart Protection Goal")
    assert not c.anchor.endswith("...")
    assert not c.anchor.endswith("…")


def test_parse_multiple_lines_and_blank_lines():
    raw = (
        "[Rituraj Singh/Pune HO/Marketing/Life]: Logic is incorrect (Context: The plan runs for...)\n"
        "\n"
        "[Sahana Rao/Bangalore/Legal Compliance and FPU/Life]: Add tax disclaimer (Context: Disclaimers:)"
    )
    out = parse_compliance_comments(raw, source_file="x.json")
    assert len(out) == 2
    assert out[1].comment == "Add tax disclaimer"
    assert out[1].anchor == "Disclaimers:"


def test_parse_empty_string_returns_empty():
    assert parse_compliance_comments("", source_file="x.json") == []
    assert parse_compliance_comments(None, source_file="x.json") == []


def test_classify_category_keywords():
    assert classify_category("Please rephrase this word") == "terminology issue"
    assert classify_category("This is not IRDAI compliant") == "legal language"
    assert classify_category("Add a source link / reference") == "missing reference"
    assert classify_category("Add the disclaimer here") == "disclaimer issue"
    assert classify_category("Not clear") == "other"


def test_classify_severity_from_reviewer():
    assert classify_severity("Shailja Saklani/Pune HO/Legal Compliance and FPU/Life") == "critical"
    assert classify_severity("Rituraj Singh/Pune HO/Marketing/Life") == "moderate"
    assert classify_severity("Microsoft Office User") == "informational"


def test_align_exact_substring_first():
    chunks = ["intro chunk text", "For instance, Bajaj Allianz Smart Protection Goal is great"]
    idx, score = align_comment_to_chunk("For instance, Bajaj Allianz Smart Protection Goal", chunks, min_fuzzy=60)
    assert idx == 1
    assert score == 100


def test_align_fuzzy_on_truncated_anchor():
    chunks = ["unrelated", "Assess whether the sum assured is suitable for your financial needs. You can use a calculator."]
    idx, score = align_comment_to_chunk("Assess whether the sum assured is suitable for your financial needs. You can use", chunks, min_fuzzy=60)
    assert idx == 1
    assert score >= 60


def test_align_returns_none_below_threshold():
    chunks = ["completely different content about taxation"]
    idx, score = align_comment_to_chunk("zzz qqq never appears", chunks, min_fuzzy=60)
    assert idx is None


# ---------------------------------------------------------------------------
# Finding #7 — word-boundary fix for classify_severity
# ---------------------------------------------------------------------------

def test_classify_severity_paralegal_not_critical():
    """'Paralegal' contains 'legal' as a substring but must NOT match \bLegal\b."""
    assert classify_severity("Paralegal Team") == "informational"


# ---------------------------------------------------------------------------
# Finding #8 — disclaimer issue takes priority over legal language
# ---------------------------------------------------------------------------

def test_classify_category_disclaimer_beats_regulatory():
    """A comment mentioning both 'disclaimer' and 'regulatory' should resolve
    to 'disclaimer issue' (more specific) now that it appears first in the
    keyword list."""
    assert classify_category("this disclaimer doesn't meet regulatory requirements") == "disclaimer issue"


# ---------------------------------------------------------------------------
# Finding #6 — _pair_final_chunk only pairs when counts match
# ---------------------------------------------------------------------------

def test_pair_final_chunk_equal_counts_returns_positional():
    """When draft and final chunk counts match, the positional chunk is returned."""
    final = ["chunk A", "chunk B", "chunk C"]
    # draft_idx=1, n_draft_chunks=3 → counts match → return final[1]
    assert _pair_final_chunk(final, 1, 3) == "chunk B"


def test_pair_final_chunk_differing_counts_returns_none():
    """When draft and final chunk counts differ, return None to avoid a wrong pairing."""
    final = ["merged chunk covering paragraphs 1 and 2", "chunk C"]
    # n_draft_chunks=3 but only 2 final chunks → mismatch → None
    assert _pair_final_chunk(final, 0, 3) is None
    assert _pair_final_chunk(final, 1, 3) is None
