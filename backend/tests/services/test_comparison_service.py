from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.services.comparison_service import (
    split_text_paragraphs,
    extract_docx_paragraphs,
    extract_pdf_paragraphs,
    extract_paragraphs,
)


def test_split_text_paragraphs_splits_on_blank_lines():
    text = "First paragraph.\n\nSecond paragraph.\n\n\nThird paragraph."
    assert split_text_paragraphs(text) == [
        "First paragraph.",
        "Second paragraph.",
        "Third paragraph.",
    ]


def test_split_text_paragraphs_empty_string_returns_empty_list():
    assert split_text_paragraphs("") == []
    assert split_text_paragraphs("   ") == []


def test_extract_docx_paragraphs_reads_paragraphs_and_tags_headings(tmp_path: Path):
    from docx import Document
    doc = Document()
    doc.add_heading("Policy Overview", level=1)
    doc.add_paragraph("This plan offers guaranteed returns.")
    doc.add_paragraph("")  # blank paragraph should be skipped
    doc.add_paragraph("Terms and conditions apply.")
    file_path = tmp_path / "sample.docx"
    doc.save(str(file_path))

    paragraphs = extract_docx_paragraphs(str(file_path))

    assert paragraphs == [
        "## Policy Overview",
        "This plan offers guaranteed returns.",
        "Terms and conditions apply.",
    ]


def test_extract_pdf_paragraphs_splits_page_text_on_blank_lines():
    fake_page = MagicMock()
    fake_page.extract_text.return_value = "Para one.\n\nPara two."
    fake_pdf = MagicMock()
    fake_pdf.__enter__.return_value.pages = [fake_page]
    fake_pdf.__exit__.return_value = False

    with patch("pdfplumber.open", return_value=fake_pdf):
        paragraphs = extract_pdf_paragraphs("fake.pdf")

    assert paragraphs == ["Para one.", "Para two."]


def test_extract_paragraphs_dispatches_by_content_type(tmp_path: Path):
    text_paragraphs = extract_paragraphs(None, "text", "Hello world.\n\nSecond line.")
    assert text_paragraphs == ["Hello world.", "Second line."]

    from docx import Document
    doc = Document()
    doc.add_paragraph("Docx body text.")
    file_path = tmp_path / "sample.docx"
    doc.save(str(file_path))
    docx_paragraphs = extract_paragraphs(str(file_path), "docx")
    assert docx_paragraphs == ["Docx body text."]


def test_extract_paragraphs_requires_file_path_for_docx_and_pdf():
    with pytest.raises(ValueError):
        extract_paragraphs(None, "docx")
    with pytest.raises(ValueError):
        extract_paragraphs(None, "pdf")


from app.services.comparison_service import word_diff, build_diff


def test_word_diff_marks_changed_and_unchanged_words():
    result = word_diff("The quick brown fox", "The slow brown fox")
    assert result["type"] == "replace"
    assert result["old_words"] == [
        {"text": "The", "changed": False},
        {"text": "quick", "changed": True},
        {"text": "brown", "changed": False},
        {"text": "fox", "changed": False},
    ]
    assert result["new_words"] == [
        {"text": "The", "changed": False},
        {"text": "slow", "changed": True},
        {"text": "brown", "changed": False},
        {"text": "fox", "changed": False},
    ]


def test_build_diff_all_equal_when_paragraphs_identical():
    old = ["First paragraph.", "Second paragraph."]
    new = ["First paragraph.", "Second paragraph."]
    assert build_diff(old, new) == [
        {"type": "equal", "old_text": "First paragraph.", "new_text": "First paragraph."},
        {"type": "equal", "old_text": "Second paragraph.", "new_text": "Second paragraph."},
    ]


def test_build_diff_pure_insert():
    assert build_diff([], ["New paragraph."]) == [
        {"type": "insert", "new_text": "New paragraph."}
    ]


def test_build_diff_pure_delete():
    assert build_diff(["Old paragraph."], []) == [
        {"type": "delete", "old_text": "Old paragraph."}
    ]


def test_build_diff_replace_same_count_runs_word_diff():
    old = ["The quick brown fox."]
    new = ["The slow brown fox."]
    blocks = build_diff(old, new)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "replace"
    assert blocks[0]["old_words"][1] == {"text": "quick", "changed": True}
    assert blocks[0]["new_words"][1] == {"text": "slow", "changed": True}


def test_build_diff_replace_different_count_falls_back_to_delete_insert():
    old = ["One paragraph that got split."]
    new = ["One paragraph.", "That got split."]
    assert build_diff(old, new) == [
        {"type": "delete", "old_text": "One paragraph that got split."},
        {"type": "insert", "new_text": "One paragraph."},
        {"type": "insert", "new_text": "That got split."},
    ]


def test_build_diff_empty_documents_produce_no_change():
    assert build_diff([], []) == []


def test_extract_paragraphs_reads_uploaded_txt_file_from_file_path(tmp_path: Path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("First paragraph.\n\nSecond paragraph.", encoding="utf-8")

    paragraphs = extract_paragraphs(str(file_path), "text")

    assert paragraphs == ["First paragraph.", "Second paragraph."]
