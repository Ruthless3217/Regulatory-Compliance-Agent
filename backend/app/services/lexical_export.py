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

from bs4 import BeautifulSoup, NavigableString, Tag
from docx import Document
from docx.document import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH

logger = logging.getLogger(__name__)

_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

# Inline tag -> the character formatting it turns on. Both spellings of each are
# listed because the two producers disagree: mammoth emits <strong>/<em> (see
# lexical_import._STYLE_MAP) and Lexical's own exportDOM emits <b>/<i>/<u>/<s>.
_INLINE_FORMATS = {
    "strong": "bold", "b": "bold",
    "em": "italic", "i": "italic",
    "u": "underline",
    "s": "strike", "strike": "strike", "del": "strike",
}

_ALIGNMENTS = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "centre": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}
_TEXT_ALIGN = re.compile(r"text-align\s*:\s*([a-z]+)", re.IGNORECASE)

# Everything but a newline: <br> becomes "\n" and must survive the collapse that
# turns HTML's incidental whitespace into single spaces.
_HORIZONTAL_WS = re.compile(r"[^\S\n]+")

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


# --- Inline content ----------------------------------------------------------
#
# A block's text cannot be read with `get_text()`. Two things are lost by it and
# both are visible in the approved document:
#
#   * the formatting — bold, italic, underline are what the editor showed, and a
#     flattened paragraph is not the document the reviewer approved;
#   * the *spacing* — `get_text(strip=True)` strips each string and joins them
#     with nothing, so "Insurance is the <b>subject</b> matter" comes back as
#     "Insurance is thesubjectmatter". Every character is present and the
#     sentence is still unreadable.
#
# So inline content is walked instead, carrying each ancestor's formatting down,
# and written as one Word run per formatting change.


def _walk_inline(el: Tag, formats: frozenset):
    """(text, formats) for `el`'s inline content, depth-first.

    Formatting is inherited: the text inside `<strong><em>` is both. `<br>`
    yields "\\n", which `_write_runs` turns into a Word line break.
    """
    for node in el.children:
        if isinstance(node, NavigableString):
            text = str(node)
            if text:
                yield text, formats
        elif isinstance(node, Tag):
            if node.name == "br":
                yield "\n", formats
            elif node.name in ("img", "ul", "ol"):
                # Pictures are embedded separately; a nested list is a block of
                # its own and is walked by _add_list, not folded into this text.
                continue
            else:
                fmt = _INLINE_FORMATS.get(node.name)
                yield from _walk_inline(node, formats | {fmt} if fmt else formats)


def _inline_pieces(el: Tag) -> List[Tuple[str, frozenset]]:
    """`el`'s inline content with HTML's whitespace rules applied.

    Runs of horizontal whitespace collapse to one space and the block's own
    leading/trailing space is dropped — what a browser renders, and what the
    reviewer therefore saw in the editor.
    """
    pieces: List[List] = []
    for text, formats in _walk_inline(el, frozenset()):
        if text != "\n":
            text = _HORIZONTAL_WS.sub(" ", text)
            # The space between two pieces belongs to whichever kept it first;
            # emitting both would double it.
            if text.startswith(" ") and pieces and pieces[-1][0].endswith((" ", "\n")):
                text = text.lstrip(" ")
        if text:
            pieces.append([text, formats])
    if pieces:
        pieces[0][0] = pieces[0][0].lstrip(" ")
        pieces[-1][0] = pieces[-1][0].rstrip(" ")
    return [(text, formats) for text, formats in pieces if text]


def _plain(el: Tag) -> str:
    """`el`'s text with its spacing intact — `get_text()` without the gluing."""
    return "".join(text for text, _ in _inline_pieces(el))


def _write_runs(paragraph, el: Tag) -> None:
    """Append `el`'s inline content to `paragraph`, one run per format change.

    A format the piece does not carry is left None rather than set False: False
    would override the paragraph style's own bold/italic, which is exactly the
    template styling the export exists to preserve.
    """
    for text, formats in _inline_pieces(el):
        for i, line in enumerate(text.split("\n")):
            if i:
                paragraph.add_run().add_break()
            if not line:
                continue
            run = paragraph.add_run(line)
            run.bold = True if "bold" in formats else None
            run.italic = True if "italic" in formats else None
            run.underline = True if "underline" in formats else None
            if "strike" in formats:
                run.font.strike = True


def _align(paragraph, el: Tag) -> None:
    """Carry an inline `text-align` across. Lexical writes the editor's
    paragraph alignment as a style attribute; without this every centred title
    exports left-aligned."""
    match = _TEXT_ALIGN.search(el.get("style") or "")
    if match:
        alignment = _ALIGNMENTS.get(match.group(1).lower())
        if alignment is not None:
            paragraph.alignment = alignment


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


def _add_list(doc: DocxDocument, el: Tag, level: int = 1) -> None:
    """A `ul`/`ol` and every list nested inside it.

    Word expresses nesting through the style name — "List Bullet 2" is the
    second level — and stops at 3, so deeper nesting flattens onto that last
    level rather than falling back to body text.
    """
    base = "List Bullet" if el.name == "ul" else "List Number"
    style = base if level == 1 else f"{base} {min(level, 3)}"
    for li in el.find_all("li", recursive=False):
        # _walk_inline skips nested lists, so this is the item's own wording.
        paragraph = _styled(doc, "", style)
        _write_runs(paragraph, li)
        for nested in li.find_all(["ul", "ol"], recursive=False):
            _add_list(doc, nested, level + 1)


def _add_block(doc: DocxDocument, el: Tag) -> None:
    _add_pictures(doc, el)
    name = el.name
    if name in _HEADING_TAGS:
        paragraph = _styled(doc, "", f"Heading {_HEADING_TAGS[name]}")
        _write_runs(paragraph, el)
        _align(paragraph, el)
    elif name in ("ul", "ol"):
        _add_list(doc, el)
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
                _write_runs(row.cells[i].paragraphs[0], cell)
    elif name == "blockquote":
        paragraph = _styled(doc, "", "Quote")
        _write_runs(paragraph, el)
    else:
        # An empty <p> is a blank line the reviewer typed, not noise: Lexical
        # writes one for every empty paragraph, and dropping them re-flows the
        # document. Only elements with no text AND no break are skipped.
        pieces = _inline_pieces(el)
        if not pieces and not el.find("br"):
            return
        paragraph = doc.add_paragraph()
        _write_runs(paragraph, el)
        _align(paragraph, el)


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
    match = _REGION_LABEL.match(_plain(el))
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
            text = _plain(el)
            if text:
                regions[current].append(text)
        else:
            _add_block(doc, el)

    for (kind, index), lines in regions.items():
        _write_region(doc, kind, index, lines)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
