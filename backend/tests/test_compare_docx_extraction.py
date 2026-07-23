"""DOCX text extraction — tracked changes and table-cell completeness.

Regression tests for content that was silently dropped from comparisons:
  - tracked-change insertions (<w:ins>) and moves (<w:moveTo>), which
    python-docx's Paragraph.text omits because it only reads direct <w:r> runs;
  - table cells lost when de-duping merged cells keyed on id(cell._tc) (an
    ephemeral lxml proxy whose id() CPython recycles, colliding distinct cells).

Deleted text (<w:del>/<w:delText>) and the old side of a move (<w:moveFrom>)
must NOT appear — they are removed content.
"""
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

import pytest

from app.services.comparison_service import (
    extract_docx_segments,
    extract_segments_labeled,
    _iter_docx_block_texts,
)


def _run(text):
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    r.append(t)
    return r


def _wrap(tag, *runs):
    """A tracked-change wrapper (<w:ins>/<w:del>/<w:moveTo>/<w:moveFrom>) around runs."""
    el = OxmlElement(tag)
    el.set(qn("w:id"), "1")
    el.set(qn("w:author"), "tester")
    for r in runs:
        el.append(r)
    return el


def _del_run(text):
    r = OxmlElement("w:r")
    dt = OxmlElement("w:delText")
    dt.text = text
    r.append(dt)
    return r


def _captured(tmp_path, doc):
    path = tmp_path / "d.docx"
    doc.save(str(path))
    return " ".join(_iter_docx_block_texts(Document(str(path))))


def test_tracked_insertion_is_captured(tmp_path):
    doc = Document()
    p = doc.add_paragraph("Base sentence has ")
    p._p.append(_wrap("w:ins", _run("INSERTEDWORD")))
    text = _captured(tmp_path, doc)
    assert "INSERTEDWORD" in text
    assert "Base sentence has" in text


def test_tracked_deletion_is_excluded(tmp_path):
    doc = Document()
    p = doc.add_paragraph("Kept text ")
    p._p.append(_wrap("w:del", _del_run("DELETEDWORD")))
    text = _captured(tmp_path, doc)
    assert "Kept text" in text
    assert "DELETEDWORD" not in text


def test_move_keeps_destination_drops_origin(tmp_path):
    doc = Document()
    p = doc.add_paragraph()
    p._p.append(_wrap("w:moveFrom", _run("MOVEDPHRASEORIGIN")))
    p2 = doc.add_paragraph()
    p2._p.append(_wrap("w:moveTo", _run("MOVEDPHRASEDEST")))
    text = _captured(tmp_path, doc)
    assert "MOVEDPHRASEDEST" in text       # surviving copy
    assert "MOVEDPHRASEORIGIN" not in text  # old location removed


def test_all_table_cells_captured_no_loss(tmp_path):
    # A wide, multi-row table: every distinct cell value must survive extraction.
    # The old id(cell._tc) de-dup could skip distinct cells (recycled proxy ids).
    doc = Document()
    rows, cols = 4, 8
    table = doc.add_table(rows=rows, cols=cols)
    expected = []
    for r in range(rows):
        for c in range(cols):
            val = f"CELL_{r}_{c}_x"
            table.cell(r, c).text = val
            expected.append(val)
    text = _captured(tmp_path, doc)
    missing = [v for v in expected if v not in text]
    assert not missing, f"table cells dropped: {missing}"


def test_sdt_wrapped_table_row_captured(tmp_path):
    # A table row wrapped in a content control (<w:sdt> around <w:tr>) is not a
    # direct <w:tr> child of the table; it must still be walked.
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "BASECELL"
    sdt = OxmlElement("w:sdt")
    sdt.append(OxmlElement("w:sdtPr"))
    content = OxmlElement("w:sdtContent")
    tr = OxmlElement("w:tr")
    tc = OxmlElement("w:tc")
    tc.append(OxmlElement("w:tcPr"))
    p = OxmlElement("w:p")
    p.append(_run("SDTROWVALUE"))
    tc.append(p)
    tr.append(tc)
    content.append(tr)
    sdt.append(content)
    table._tbl.append(sdt)
    text = _captured(tmp_path, doc)
    assert "BASECELL" in text and "SDTROWVALUE" in text


def test_unreadable_docx_gives_friendly_error(tmp_path):
    # A non-OOXML file with a .docx name must fail with an actionable message,
    # not a raw python-docx PackageNotFoundError.
    bad = tmp_path / "not-really.docx"
    bad.write_text("this is plain text, not a zip-based .docx")
    with pytest.raises(ValueError) as ei:
        extract_segments_labeled(str(bad), "docx", None, "original")
    msg = str(ei.value)
    assert "original document" in msg and ".docx" in msg


def test_block_content_control_captured(tmp_path):
    # Text inside a block-level content control (<w:sdt>) is not a direct body
    # <w:p>/<w:tbl>, so a naive body walk skips it entirely.
    doc = Document()
    body = doc.element.body
    sect = body.find(qn("w:sectPr"))
    sdt = OxmlElement("w:sdt")
    sdt.append(OxmlElement("w:sdtPr"))
    content = OxmlElement("w:sdtContent")
    p = OxmlElement("w:p")
    p.append(_run("SDTBLOCKVALUE"))
    content.append(p)
    sdt.append(content)
    sect.addprevious(sdt)
    text = _captured(tmp_path, doc)
    assert "SDTBLOCKVALUE" in text


def test_textbox_content_captured(tmp_path):
    # Text-box text lives in <w:txbxContent> nested in a run's drawing; it is
    # invisible to Paragraph.text and to a plain body/table walk.
    doc = Document()
    body = doc.element.body
    sect = body.find(qn("w:sectPr"))
    anchor = OxmlElement("w:p")
    run = OxmlElement("w:r")
    pict = OxmlElement("w:pict")
    txbx = OxmlElement("w:txbxContent")
    inner = OxmlElement("w:p")
    inner.append(_run("TEXTBOXVALUE"))
    txbx.append(inner)
    pict.append(txbx)
    run.append(pict)
    anchor.append(run)
    sect.addprevious(anchor)
    text = _captured(tmp_path, doc)
    assert "TEXTBOXVALUE" in text


def test_vertically_merged_cell_not_duplicated(tmp_path):
    # A vertically merged cell shares one <w:tc>; its text must appear exactly once.
    doc = Document()
    table = doc.add_table(rows=3, cols=2)
    table.cell(0, 0).text = "MERGEDVALUE"
    table.cell(0, 0).merge(table.cell(2, 0))  # merge down column 0, rows 0..2
    for r in range(3):
        table.cell(r, 1).text = f"RIGHT_{r}"
    segments = extract_docx_segments(str(_save(tmp_path, doc)))
    joined = " ".join(segments)
    assert joined.count("MERGEDVALUE") == 1
    for r in range(3):
        assert f"RIGHT_{r}" in joined


def _save(tmp_path, doc):
    path = tmp_path / "merged.docx"
    doc.save(str(path))
    return path
