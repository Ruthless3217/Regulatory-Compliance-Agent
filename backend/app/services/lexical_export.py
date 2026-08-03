"""HTML -> DOCX, the export half of the Lexical working document.

Runs server-side rather than in the browser so an export is reproducible and
auditable: the approved artifact must not depend on which machine produced it.
Consumes the same HTML vocabulary lexical_import emits.

The upload is also the export's *template*. Building a fresh ``Document()``
returns a default-styled Word file, which loses the original's fonts, styles,
numbering, page setup, headers, footers and embedded media — "put a docx in,
get the same docx back" is the product promise, so instead the original is
cloned in memory and only its body content is replaced. The uploaded file
itself is opened read-only and never written; it stays the immutable original.
"""
from __future__ import annotations

import base64
import io
import logging
import re
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup, Tag
from docx import Document
from docx.document import Document as DocxDocument

logger = logging.getLogger(__name__)

_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

# lexical_import appends each header/footer to the body as a labelled
# `<h3>Page header</h3>` / `<h3>Page footer 2</h3>` section so the reviewer can
# read and correct text that mammoth does not convert. Those must go back where
# they came from — writing them out as body paragraphs would relocate a mandated
# disclaimer from the footer into the body of the approved document.
_REGION_LABEL = re.compile(r"^Page (header|footer)(?: (\d+))?$", re.IGNORECASE)


def _styled(doc: DocxDocument, text: str, style: str):
    """A paragraph in `style`, or an unstyled one if the template lacks it.

    A template need not define every built-in style, and a missing one raises
    KeyError. Losing the styling of one paragraph beats losing the export.
    """
    try:
        return doc.add_paragraph(text, style=style)
    except KeyError:
        logger.info("lexical_export: template defines no %r style", style)
        return doc.add_paragraph(text)


def _add_pictures(doc: DocxDocument, el: Tag) -> None:
    """Embed every image in `el`. mammoth emits base64 `data:` URIs."""
    for img in [el] if el.name == "img" else el.find_all("img"):
        src = img.get("src") or ""
        try:
            meta, _, payload = src.partition(",")
            if not meta.startswith("data:") or not payload:
                raise ValueError(f"unsupported src {src[:40]!r}")
            doc.add_picture(io.BytesIO(base64.b64decode(payload)))
        except Exception as exc:  # noqa: BLE001 — a bad image must not lose the document
            logger.warning("lexical_export: skipping image: %s", exc)


def _add_block(doc: DocxDocument, el: Tag) -> None:
    _add_pictures(doc, el)
    name = el.name
    if name in _HEADING_TAGS:
        _styled(doc, el.get_text(strip=True), f"Heading {_HEADING_TAGS[name]}")
    elif name in ("ul", "ol"):
        style = "List Bullet" if name == "ul" else "List Number"
        for li in el.find_all("li", recursive=False):
            _styled(doc, li.get_text(strip=True), style)
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
        _styled(doc, el.get_text(strip=True), "Quote")
    else:
        text = el.get_text(strip=True)
        if text:
            doc.add_paragraph(text)


def _open_template(template_path: Optional[str]) -> DocxDocument:
    """The uploaded document with its body emptied, or a default document.

    `sectPr` — the last child of `w:body` — carries page size, margins and the
    references to the header/footer parts, so it is the one block-level element
    that must survive the emptying.
    """
    if not template_path:
        return Document()
    try:
        with open(template_path, "rb") as f:  # read-only: the upload is immutable
            doc = Document(io.BytesIO(f.read()))
    except Exception as exc:  # noqa: BLE001 — a missing or corrupt upload still exports
        logger.warning(
            "lexical_export: template %s unusable, using default styles: %s",
            template_path,
            exc,
        )
        return Document()

    body = doc.element.body
    for child in list(body):
        if not child.tag.endswith("}sectPr"):
            body.remove(child)
    return doc


def _region_label(el: Tag) -> Optional[Tuple[str, int]]:
    """(kind, 1-based section number) if `el` labels a header/footer section."""
    if el.name != "h3":
        return None
    match = _REGION_LABEL.match(el.get_text(strip=True))
    if not match:
        return None
    return match.group(1).lower(), int(match.group(2) or 1)


def _write_region(doc: DocxDocument, kind: str, index: int, lines: List[str]) -> None:
    """Put `lines` back into section `index`'s header or footer."""
    if not lines:
        return
    try:
        section = doc.sections[index - 1]
    except IndexError:
        # Unmappable: keep the template's own wording rather than guess, and
        # never fall back to writing it into the body.
        logger.warning(
            "lexical_export: no section %d for its page %s; left the original in place",
            index,
            kind,
        )
        return
    region = getattr(section, kind)
    existing = region.paragraphs
    for paragraph in existing[1:]:
        paragraph._element.getparent().remove(paragraph._element)
    first = existing[0] if existing else region.add_paragraph()
    first.text = lines[0]
    for line in lines[1:]:
        region.add_paragraph(line)


def lexical_html_to_docx(
    html: str, title: str, template_path: str | None = None
) -> bytes:
    """DOCX bytes for the editor's HTML. An empty document is valid output.

    With a readable `template_path` the export is the uploaded document with its
    body replaced, so styles, numbering, page setup and headers/footers carry
    over. Without one (a pasted-text submission, or an upload that no longer
    reads) it is a default-styled document, as before.
    """
    doc = _open_template(template_path)
    soup = BeautifulSoup(html or "", "html.parser")

    # Everything after a header/footer label belongs to that region — the
    # importer appends them, in order, at the end of the body.
    regions: dict = {}
    current: Optional[Tuple[str, int]] = None
    for el in soup.find_all(recursive=False):
        if not isinstance(el, Tag):
            continue
        label = _region_label(el)
        if label is not None:
            current = label
            regions.setdefault(current, [])
        elif current is not None:
            text = el.get_text(strip=True)
            if text:
                regions[current].append(text)
        else:
            _add_block(doc, el)

    for (kind, index), lines in regions.items():
        _write_region(doc, kind, index, lines)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
