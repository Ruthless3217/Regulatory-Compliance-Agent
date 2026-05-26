"""Pure-unit tests for knowledge-base ingestion parsing/classification/alignment."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.knowledge_base_ingestion import (
    parse_compliance_comments,
    classify_category,
    classify_severity,
    align_comment_to_chunk,
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
