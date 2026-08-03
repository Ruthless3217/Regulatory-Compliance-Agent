"""HTML -> DOCX, the export half of the Lexical working document.

Runs server-side rather than in the browser so an export is reproducible and
auditable: the approved artifact must not depend on which machine produced it.
Consumes the same HTML vocabulary lexical_import emits.
"""
from __future__ import annotations

import io
from typing import List

from bs4 import BeautifulSoup, Tag
from docx import Document
from docx.document import Document as DocxDocument

_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


def _add_block(doc: DocxDocument, el: Tag) -> None:
    name = el.name
    if name in _HEADING_TAGS:
        doc.add_heading(el.get_text(strip=True), level=_HEADING_TAGS[name])
    elif name in ("ul", "ol"):
        style = "List Bullet" if name == "ul" else "List Number"
        for li in el.find_all("li", recursive=False):
            doc.add_paragraph(li.get_text(strip=True), style=style)
    elif name == "table":
        rows: List[Tag] = el.find_all("tr")
        if not rows:
            return
        cols = max(len(r.find_all(["td", "th"])) for r in rows)
        table = doc.add_table(rows=0, cols=cols)
        for r in rows:
            cells = r.find_all(["td", "th"])
            row = table.add_row()
            for i, cell in enumerate(cells[:cols]):
                row.cells[i].text = cell.get_text(strip=True)
    elif name == "blockquote":
        doc.add_paragraph(el.get_text(strip=True), style="Quote")
    else:
        text = el.get_text(strip=True)
        if text:
            doc.add_paragraph(text)


def lexical_html_to_docx(html: str, title: str) -> bytes:
    """DOCX bytes for the editor's HTML. An empty document is valid output."""
    doc = Document()
    soup = BeautifulSoup(html or "", "html.parser")
    for el in soup.find_all(recursive=False):
        if isinstance(el, Tag):
            _add_block(doc, el)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
