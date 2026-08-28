"""HTML -> DOCX, the export half of the Lexical working document.

Runs server-side rather than in the browser so an export is reproducible and
auditable: the approved artifact must not depend on which machine produced it.
Consumes the same HTML vocabulary lexical_import emits.

The upload is also the export's *template*. Building a fresh ``Document()``
returns a default-styled Word file, which loses the original's fonts, styles,
numbering, page setup, headers, footers and embedded media — "put a docx in,
get the same docx back" is the product promise, so instead the original is
cloned in memory and its content is *edited in place*: every paragraph, table
and run keeps its own formatting and only the words inside it are rewritten
(see ``docx_body_patch``). The uploaded file itself is opened read-only and
never written; it stays the immutable original.

Without a template — a pasted-text submission, or a PDF, which has no DOCX to
preserve — there is nothing to edit, so the document is built from scratch out
of the HTML's semantics. That path is the one below; it produces a correct,
default-styled Word file and cannot produce anything better, because the
formatting was never in the HTML to begin with.
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup, NavigableString, Tag
from docx import Document
from docx.document import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from app.services import docx_body_patch as patch
from app.services.docx_body_patch import EditedParagraph, EditedTable

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

# lexical_import appends each header, footer and unconverted text box to the
# body as a labelled `<h3>Page header</h3>` / `<h3>Page footer 2</h3>` /
# `<h3>Text box</h3>` section so the reviewer can read and correct text that
# mammoth does not convert. Those must go back where they came from — writing
# them out as body paragraphs would relocate a mandated disclaimer out of the
# footer, or out of the box it was set in, into the body of the approved
# document, and leave the label itself in the file as a heading.
_REGION_LABEL = re.compile(
    r"^(?:Page (header|footer)|(Text box))(?: (\d+))?$", re.IGNORECASE
)


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


def _decode_image(src: str) -> Optional[bytes]:
    """The bytes behind a `data:` URI, or None for anything else."""
    meta, _, payload = src.partition(",")
    if not meta.startswith("data:") or not payload:
        logger.warning("lexical_export: skipping image: unsupported src %r", src[:40])
        return None
    try:
        return base64.b64decode(payload)
    except Exception as exc:  # noqa: BLE001 — a bad image must not lose the document
        logger.warning("lexical_export: skipping image: %s", exc)
        return None


def _prune_unchanged_images(doc: DocxDocument, blocks: List) -> None:
    """Forget the pictures the document already has, before anything aligns.

    mammoth hands the upload's OWN pictures back as base64, so an image-only
    `<p>` in the editor is normally just the picture already sitting in the
    body — whose paragraph carries no text and is therefore not in the
    alignment at all. Left in, that block matches nothing and is inserted: a
    blank paragraph appended on every single export, cloned from its neighbour,
    so in a numbered document it arrives wearing that neighbour's ``numPr`` and
    Word draws it as an empty bullet.

    What survives here is only what the reviewer actually added.
    """
    existing = {part.sha1 for part in doc.part.package.image_parts}
    for block in blocks:
        srcs = getattr(block, "images", None)
        if not srcs:
            continue
        block.images = [
            src for src in srcs
            if (blob := _decode_image(src)) is not None
            and hashlib.sha1(blob).hexdigest() not in existing
        ]


def _place_new_images(doc: DocxDocument, placements: List[Tuple[object, object]]) -> None:
    """Embed the pictures the reviewer ADDED, where they added them.

    The upload's own pictures are already in the document — the patch left
    their runs alone — and mammoth hands every one of them back to the editor
    as a base64 `data:` URI, so the HTML cannot tell an added picture from an
    original one. The image parts can: a picture already in the package is one
    the document came with, and re-embedding it would print the logo twice.

    Compared by raw digest rather than by ``Image.from_blob``, which is how
    ``ImagePart`` computes its own ``sha1`` anyway. python-docx cannot identify
    every format Word embeds — the EMF and WMF vector logos in these brochures
    raise ``UnrecognizedImageError`` — and one of those must still be
    recognised as the original it is, not reported as a picture we dropped.
    """
    existing = {part.sha1 for part in doc.part.package.image_parts}
    for block, element in placements:
        for src in getattr(block, "images", ()):
            blob = _decode_image(src)
            if blob is None:
                continue
            sha1 = hashlib.sha1(blob).hexdigest()
            if sha1 in existing:
                continue  # the document came with it; it is still in place
            try:
                doc.add_picture(io.BytesIO(blob))
            except Exception as exc:  # noqa: BLE001 — a bad image must not lose the document
                logger.warning("lexical_export: skipping image: %r", exc)
                continue
            existing.add(sha1)
            # Moved out of the paragraph add_picture made and into the block's
            # own, so the picture sits where the reviewer put it.
            holder = doc.paragraphs[-1]._element
            for run in list(holder):
                if run.tag == qn("w:r"):
                    element.append(run)
            holder.getparent().remove(holder)


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


def _open_template(template_path: Optional[str]) -> Tuple[DocxDocument, bool]:
    """(document, is_the_upload). The uploaded document, body and all, or a
    default one when there is no usable upload to preserve.

    The body is NOT emptied. Emptying it was the whole problem: a body's own
    fonts, sizes, colours, indents and table styles live on the body's own XML
    and are not in the HTML to write back. It is edited instead — see
    ``docx_body_patch``.
    """
    if not template_path:
        return Document(), False
    try:
        with open(template_path, "rb") as f:  # read-only: the upload is immutable
            return Document(io.BytesIO(f.read())), True
    except Exception as exc:  # noqa: BLE001 — a missing or corrupt upload still exports
        logger.warning(
            "lexical_export: template %s unusable, using default styles: %s",
            template_path,
            exc,
        )
        return Document(), False


# --- The editor's blocks, as data ---------------------------------------------
#
# `_add_block` writes soup straight into a fresh document. The patch path
# cannot: it has to line the reviewer's blocks up against the upload's own
# paragraphs before anything is written, so the HTML is read into the plain
# (text, formats) structures `docx_body_patch` aligns on.

_CELL_BLOCKS = ("p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "div")


def _edited_blocks(el: Tag, blocks: List) -> None:
    """Append `el` to `blocks` as one entry per *paragraph* — the unit Word
    stores. A list is its items, not a single block: each ``<li>`` is a ``w:p``
    of its own and has to align with one."""
    if el.name in ("ul", "ol"):
        for li in el.find_all("li", recursive=False):
            blocks.append(EditedParagraph(_inline_pieces(li), _image_sources(li)))
            for nested in li.find_all(["ul", "ol"], recursive=False):
                _edited_blocks(nested, blocks)
    elif el.name == "table":
        blocks.append(
            EditedTable([
                [_edited_cell(cell) for cell in row.find_all(["td", "th"])]
                for row in el.find_all("tr")
            ])
        )
    else:
        blocks.append(EditedParagraph(_inline_pieces(el), _image_sources(el)))


def _image_sources(el: Tag) -> List[str]:
    return [src for src in (img.get("src") or "" for img in el.find_all("img")) if src]


def _edited_cell(cell: Tag) -> List[EditedParagraph]:
    """A cell's paragraphs. mammoth wraps cell text in ``<p>``; Lexical's own
    export does not, so a cell with neither is read as one paragraph."""
    inner = cell.find_all(_CELL_BLOCKS, recursive=False)
    if inner:
        return [EditedParagraph(_inline_pieces(child)) for child in inner]
    return [EditedParagraph(_inline_pieces(cell))]


def _region_label(el: Tag) -> Optional[Tuple[str, int]]:
    """(kind, 1-based number) if `el` labels a header, footer or text box."""
    if el.name != "h3":
        return None
    match = _REGION_LABEL.match(_plain(el))
    if not match:
        return None
    kind = "textbox" if match.group(2) else match.group(1).lower()
    return kind, int(match.group(3) or 1)


def _write_region(
    doc: DocxDocument, kind: str, index: int, blocks: List, boxes: List
) -> None:
    """Put `blocks` back into the region they were imported from.

    Patched, not rewritten: a footer's mandated disclaimer is usually set small
    and grey by hand, and re-typing the line as plain text would return it in
    the body font.
    """
    if not blocks:
        return
    container = _region_container(doc, kind, index, boxes)
    if container is None:
        # Unmappable: keep the template's own wording rather than guess, and
        # never fall back to writing it into the body.
        logger.warning(
            "lexical_export: no %s %d to write back to; left the original in place",
            kind,
            index,
        )
        return
    patch.patch_container(container, blocks)


def _region_container(doc: DocxDocument, kind: str, index: int, boxes: List):
    """The XML element behind one labelled region, or None if it is gone."""
    if index < 1:
        return None  # "Page footer 0" must not resolve to sections[-1]
    if kind == "textbox":
        return boxes[index - 1] if index - 1 < len(boxes) else None
    try:
        return getattr(doc.sections[index - 1], kind)._element
    except IndexError:
        return None


def _importable_textboxes(doc: DocxDocument) -> List:
    """The text boxes ``lexical_import`` labelled, in the order it numbered
    them.

    It appends only the boxes mammoth did NOT convert — a box whose wording is
    already in the converted body is left out, so it is not imported twice —
    and numbers what is left from 1. Reproducing that filter here is what makes
    "Text box 2" name the same box on the way back out.
    """
    body_text = patch.normalize(patch.container_text(doc.element.body))
    boxes = []
    for box in doc.element.body.iter(qn("w:txbxContent")):
        lines = [
            text
            for text in (patch.normalize(patch.paragraph_text(p)) for p in box.iter(qn("w:p")))
            if text
        ]
        if lines and not all(line in body_text for line in lines):
            boxes.append(box)
    return boxes


def lexical_html_to_docx(
    html: str, title: str, template_path: str | None = None
) -> bytes:
    """DOCX bytes for the editor's HTML. An empty document is valid output.

    With a readable `template_path` the export IS the uploaded document, with
    the reviewer's corrections written into it — every style, font, colour,
    indent, table and page setting the upload had, it still has. Without one (a
    pasted-text submission, a PDF, or an upload that no longer reads) the
    document is built from the HTML's semantics on default styles instead.
    """
    doc, patching = _open_template(template_path)
    soup = BeautifulSoup(html or "", "html.parser")
    # Enumerated before the body is touched, because the filter that numbers
    # them reads the body's text.
    boxes = _importable_textboxes(doc) if patching else []

    # Everything after a region label belongs to that region — the importer
    # appends them, in order, at the end of the body.
    regions: dict = {}
    body: List[Tag] = []
    current: Optional[Tuple[str, int]] = None
    for el in soup.find_all(recursive=False):
        if not isinstance(el, Tag):
            continue
        label = _region_label(el)
        if label is not None:
            current = label
            regions.setdefault(current, [])
        elif current is not None:
            _edited_blocks(el, regions[current])
        else:
            body.append(el)

    if patching:
        blocks: List = []
        for el in body:
            _edited_blocks(el, blocks)
        _prune_unchanged_images(doc, blocks)
        for region_blocks in regions.values():
            _prune_unchanged_images(doc, region_blocks)
        _place_new_images(doc, patch.patch_container(doc.element.body, blocks))
    else:
        for el in body:
            _add_block(doc, el)

    for (kind, index), blocks in regions.items():
        _write_region(doc, kind, index, blocks, boxes)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
