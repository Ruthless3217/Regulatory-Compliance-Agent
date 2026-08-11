"""DOCX/PDF -> HTML, the import half of the Lexical working document.

The uploaded file is immutable, so this runs once at upload and its output is
converted to a Lexical state that the reviewer edits from then on. Fidelity
lost here is lost permanently, which is why the tests assert on structure
(headings, lists, tables) rather than on text.

mammoth is used rather than a hand-rolled OOXML walk: it already maps Word
styles onto semantic HTML, and the reverse direction (lexical_export) consumes
the same vocabulary.

Fidelity: DOCX and PDF are not equivalent
-----------------------------------------
A DOCX carries its own semantics — a heading IS a heading, a list IS a list —
so ``docx_to_html`` reads structure rather than guessing it, and on the way out
``submission_export_service`` hands the original file back as the export's
*template*, so the corrected document returns in the shape it arrived in.

A PDF carries none of that. It is a page-description format: positioned glyphs,
with no paragraphs, no headings, no lists, no reading order. ``pdf_to_html``
therefore produces a BEST-EFFORT RECONSTRUCTION inferred from geometry and
typography, and the following is lost and will not come back:

  * page layout, margins, columns (multi-column pages will interleave),
  * tables (they flatten into loose lines of text),
  * images, logos, colour, and every font choice,
  * anything with no text layer — a scanned or outlined PDF raises rather than
    returning an empty document.

Consequently a PDF-sourced export does NOT match the original's layout. There
is no template to clone, so the corrected file is rebuilt on a blank Word
document: the *words* are preserved and correctable, the *design* is not. This
is the honest ceiling of the feature, not a bug to be tuned away — reviewers
approving a PDF creative should read the export as corrected copy, not as a
faithful reproduction of the artwork.
"""
from __future__ import annotations

import io
import logging
import re
from collections import Counter
from statistics import median

logger = logging.getLogger(__name__)


class LexicalImportError(RuntimeError):
    """The upload could not be converted. Callers fall back to extracted text."""


# Word style -> HTML element. Explicit so an unmapped style is visible in
# review rather than silently flattening to <p>.
_STYLE_MAP = """
p[style-name='Heading 1'] => h1:fresh
p[style-name='Heading 2'] => h2:fresh
p[style-name='Heading 3'] => h3:fresh
p[style-name='Heading 4'] => h4:fresh
p[style-name='Title'] => h1:fresh
p[style-name='Quote'] => blockquote:fresh
b => strong
i => em
u => u
"""


def docx_to_html(docx_bytes: bytes) -> str:
    """Body-only HTML fragment for `docx_bytes`.

    Raises LexicalImportError rather than returning an empty string: an empty
    document and a failed conversion must not look identical to the caller.
    """
    if not docx_bytes:
        raise LexicalImportError("empty upload")
    try:
        import mammoth

        result = mammoth.convert_to_html(
            io.BytesIO(docx_bytes), style_map=_STYLE_MAP
        )
    except Exception as exc:  # noqa: BLE001 — any parse failure is one outcome
        raise LexicalImportError(str(exc)) from exc

    for message in result.messages:
        logger.info("lexical_import: %s", message)
    return result.value + _peripheral_html(docx_bytes, result.value)


def _peripheral_html(docx_bytes: bytes, body_html: str) -> str:
    """Headers, footers and the text boxes mammoth did not read.

    mammoth converts the document body only. `preprocessing_service._extract_docx`
    deliberately includes these regions because mandated disclaimers live in
    exactly them — so the compliance engine grades text the editor would
    otherwise not contain.

    That gap is not cosmetic. A finding quoting a footer could not be located in
    the editor, could not be corrected there, and — because `clean.docx` is
    generated from the editor's HTML — would have been dropped from the exported
    artifact entirely, losing the disclaimer from the approved document.

    Appended at the end, labelled, rather than positioned: Word headers and
    footers repeat per section and have no single place in a linear document.
    Being able to read and correct them matters more than their position.
    """
    try:
        from docx import Document

        doc = Document(io.BytesIO(docx_bytes))
    except Exception as exc:  # noqa: BLE001 — body already converted; keep it
        logger.warning("lexical_import: peripheral extraction skipped: %s", exc)
        return ""

    return "".join(
        f"<h3>{label}</h3>" + "".join(f"<p>{_escape(l)}</p>" for l in lines)
        for label, lines in _iter_peripheral_regions(doc, html_to_text(body_html))
    )


def _iter_peripheral_regions(doc, body_text: str):
    """(label, lines) for every header, footer and unconverted text box.

    Word gives each section its own header and footer, and an unlinked section
    can carry different wording — a different disclaimer on a different page.

    Text boxes are anchored in the body but sit outside the run tree, and a
    mandated disclaimer is very often set in one — the same reason
    `preprocessing_service` extracts them for grading. mammoth reads SOME of
    them (`v:textbox` and `w:txbxContent` are in its element map, so a VML box
    and the VML fallback Word writes inside `mc:AlternateContent` both convert)
    but not a bare DrawingML `wps:txbx`, which it walks into and does not
    recognise. Hence `body_text`: only a box whose wording is not already in the
    converted body is appended here. Emitting them all instead would duplicate
    the disclaimer in the editor, in `clean.docx` and in the redline — and a
    duplicated mandated disclaimer is its own compliance problem.

    Deduplicated by text so an unchanged repeat is not emitted once per section.
    """
    seen: set[str] = set()

    def is_new(lines: list[str]) -> bool:
        signature = "\n".join(lines).strip()
        if not signature or signature in seen:
            return False
        seen.add(signature)
        return True

    sections = doc.sections
    for i, section in enumerate(sections, start=1):
        for kind, region in (("header", section.header), ("footer", section.footer)):
            try:
                lines = _region_lines(region)
            except Exception:  # noqa: BLE001 — a malformed part must not abort the rest
                continue
            if is_new(lines):
                suffix = "" if len(sections) == 1 else f" {i}"
                yield f"Page {kind}{suffix}", lines

    boxes = [
        lines
        for lines in _textbox_blocks(doc)
        if not all(line in body_text for line in lines)
    ]
    for i, lines in enumerate(boxes, start=1):
        if is_new(lines):
            suffix = "" if len(boxes) == 1 else f" {i}"
            yield f"Text box{suffix}", lines


def _region_lines(region) -> list[str]:
    """Paragraph and table text of one header/footer, blank lines dropped."""
    lines = [p.text.strip() for p in region.paragraphs if p.text.strip()]
    for table in getattr(region, "tables", []):
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    return lines


def _textbox_blocks(doc) -> list[list[str]]:
    """The lines of each text box in the body, one list per box.

    Mirrors `preprocessing_service._docx_textbox_lines`, which is what the
    analyser grades: VML (`w:pict`) and DrawingML (`w:drawing`) both nest their
    content in `w:txbxContent`, and `w:moveFrom` residue is skipped so a moved
    run's stale source is not imported twice.

    The caller drops the ones mammoth already converted. What is left is text
    the analyser graded and the editor did not contain, so a finding on it could
    not be corrected, `clean.docx` dropped it, and the redline read every line
    of it as a reviewer deletion.
    """
    try:
        from docx.oxml.ns import qn

        move_from = qn("w:moveFrom")
        blocks: list[list[str]] = []
        for txbx in doc.element.body.iter(qn("w:txbxContent")):
            lines: list[str] = []
            for p in txbx.iter(qn("w:p")):
                text = "".join(
                    t.text or ""
                    for t in p.iter(qn("w:t"))
                    if not any(a.tag == move_from for a in t.iterancestors())
                ).strip()
                if text:
                    lines.append(text)
            if lines:
                blocks.append(lines)
        return blocks
    except Exception as exc:  # noqa: BLE001 — body already converted; keep it
        logger.warning("lexical_import: text-box extraction skipped: %s", exc)
        return []


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


# --- PDF ---------------------------------------------------------------------
#
# Every heuristic below is borrowed rather than invented, so a PDF is read the
# same way everywhere in the app:
#   * ``comparison_service._detect_running_lines`` — repeated top/bottom-of-page
#     lines are chrome, not content (needs >= 3 pages to tell them apart).
#   * ``comparison_service._PAGE_NUMBER`` — "3", "Page 3", "3 of 21".
#   * ``comparison_service._HYPHEN_WRAP`` + the unwrap recipe from
#     ``extract_pdf_segments`` — rejoin "insur-\nance" and flow physical lines.
#   * ``preprocessing_service.ContextEngineeringService._is_heading_line`` — the
#     one heading heuristic in the codebase.
#
# That last one is deliberately PERMISSIVE where it is used, because a false
# positive there is absorbed by the MIN_SECTION_TOKENS merge. Nothing absorbs it
# here: a paragraph promoted to <h2> is invented structure the reviewer then has
# to un-invent. So it is used as a NECESSARY condition only, and must be
# corroborated by typography the PDF actually carries — a larger font or a fully
# bold line. Text shape alone is never enough; a document with uniform
# typography comes out flat, which is the correct answer.


def pdf_to_html(pdf_bytes: bytes) -> str:
    """Body-only HTML fragment reconstructed from `pdf_bytes`.

    Best-effort by nature — see the fidelity note in the module docstring. Like
    ``docx_to_html`` this raises LexicalImportError rather than returning an
    empty string, so "nothing to edit" and "conversion failed" stay
    distinguishable to the caller (which degrades to extracted text either way).
    """
    if not pdf_bytes:
        raise LexicalImportError("empty upload")
    try:
        return _pdf_to_html(pdf_bytes)
    except LexicalImportError:
        raise
    except Exception as exc:  # noqa: BLE001 — any parse failure is one outcome
        raise LexicalImportError(str(exc)) from exc


def _pdf_to_html(pdf_bytes: bytes) -> str:
    import pdfplumber

    from app.services.comparison_service import (
        _HYPHEN_WRAP,
        _PAGE_NUMBER,
        _detect_running_lines,
    )

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = [_pdf_page_lines(page) for page in pdf.pages]

    running = _detect_running_lines([[ln["text"] for ln in p] for p in pages])
    pages = [
        [
            ln for ln in p
            if ln["text"] not in running and not _PAGE_NUMBER.match(ln["text"])
        ]
        for p in pages
    ]
    if not any(pages):
        # A scanned/outlined PDF. preprocessing_service OCRs this for grading,
        # but OCR output is not text a reviewer should be editing and exporting
        # as the approved wording, so the editor is simply not offered.
        raise LexicalImportError("no text layer to reconstruct")

    # The one lossy case worth seeing in production rather than guessing at: a
    # multi-column page reads left-to-right across the gutter and interleaves.
    columnar = sum(line["columnar"] for p in pages for line in p)
    total = sum(len(p) for p in pages)
    if total and columnar / total > 0.3:
        logger.warning(
            "lexical_import: %d/%d PDF lines span columns; the reconstruction "
            "will interleave them", columnar, total,
        )

    body_size = _pdf_body_size(pages)
    html: list[str] = []
    for block in _pdf_blocks(pages, body_size):
        if len(block) == 1 and _pdf_is_heading(block[0], body_size):
            html.append(f"<h2>{_escape(block[0]['text'])}</h2>")
            continue
        text = "\n".join(ln["text"] for ln in block)
        text = _HYPHEN_WRAP.sub(r"\1\2", text)  # join hyphenated line-wraps
        text = text.replace("\n", " ").strip()  # unwrap physical lines
        if text:
            html.append(f"<p>{_escape(text)}</p>")
    return "".join(html)


def _pdf_page_lines(page) -> list[dict]:
    """Text lines of one page with the typography needed to judge them."""
    try:
        lines = page.extract_text_lines()
    except Exception as exc:  # noqa: BLE001 — one bad page must not lose the rest
        logger.warning("lexical_import: PDF page skipped: %s", exc)
        return []

    out: list[dict] = []
    for line in lines:
        text = (line.get("text") or "").strip()
        if not text:
            continue
        chars = line.get("chars") or []
        sizes = [c.get("size") or 0.0 for c in chars]
        fonts = [str(c.get("fontname") or "") for c in chars]
        size = round(max(sizes), 1) if sizes else 0.0
        out.append({
            "text": text,
            "top": line.get("top") or 0.0,
            "bottom": line.get("bottom") or 0.0,
            "size": size,
            # All-or-nothing: a line with one bold word is emphasis, not a title.
            "bold": bool(fonts) and all("bold" in f.lower() for f in fonts),
            # A gutter this wide means the "line" is really several cells or
            # columns sharing a baseline — see _pdf_is_heading. Well above even
            # generous justified word-spacing (~1.5x size), far below a real
            # table gutter.
            "columnar": _pdf_max_gutter(chars) > 2.5 * size if size else False,
        })
    return out


def _pdf_max_gutter(chars: list[dict]) -> float:
    """Widest horizontal gap between consecutive glyphs on one line."""
    xs = sorted((c.get("x0") or 0.0, c.get("x1") or 0.0) for c in chars)
    return max((b[0] - a[1] for a, b in zip(xs, xs[1:])), default=0.0)


def _pdf_body_size(pages: list[list[dict]]) -> float:
    """The document's body font size — the size most of its *characters* are
    set in. Weighted by characters, not lines, so a page of short headings
    cannot outvote the prose."""
    sizes: Counter = Counter()
    for lines in pages:
        for line in lines:
            sizes[line["size"]] += len(line["text"])
    return sizes.most_common(1)[0][0] if sizes else 0.0


def _pdf_blocks(pages: list[list[dict]], body_size: float):
    """Group lines into paragraph blocks on vertical whitespace.

    A PDF has no paragraph marks, so the only evidence of one is a gap taller
    than the page's own line spacing. The median gap IS that line spacing in
    prose-heavy copy, which is what this app sees.

    A heading line always stands alone, regardless of gap: headings are normally
    set *tight* against the copy they introduce, so waiting for a paragraph-sized
    gap beneath one would swallow it into the following paragraph.

    ponytail: median-gap heuristic, per page. It merges paragraphs in
    double-spaced text and splits them across a column or page break. Upgrade
    path is clustering gaps (or reading `doctop` runs) if real uploads show it
    failing — but the failure is a mis-split paragraph in an editor the reviewer
    can fix, not lost or corrupted text.
    """
    for lines in pages:
        gaps = [b["top"] - a["bottom"] for a, b in zip(lines, lines[1:])]
        threshold = 1.5 * median(gaps) if gaps else 0.0
        block: list[dict] = []
        was_heading = False
        for i, line in enumerate(lines):
            heading = _pdf_is_heading(line, body_size)
            if block and (heading or was_heading or gaps[i - 1] > threshold):
                yield block
                block = []
            block.append(line)
            was_heading = heading
        if block:
            yield block


def _pdf_is_heading(line: dict, body_size: float) -> bool:
    """A heading both reads like one and is *set* like one.

    ``columnar`` is the veto that matters in practice: a bold table header row
    ("Policy year   Allocation charge   Fund value") is indistinguishable from a
    heading once flattened to text, and these documents are full of charge
    tables. Its column gutters give it away.
    """
    from app.services.preprocessing_service import ContextEngineeringService

    if line["columnar"]:
        return False
    if not ContextEngineeringService._is_heading_line(line["text"]):
        return False
    if line["bold"]:
        return True
    # body_size 0 means the PDF reported no font sizes at all — no evidence, so
    # no headings, rather than promoting every heading-shaped line.
    return body_size > 0 and line["size"] >= body_size * 1.15


# --- HTML -> text ------------------------------------------------------------

# Block-level tags of the import vocabulary (plus the ones Lexical writes back).
# Anything else is inline and belongs to the block it sits in.
_BLOCK_TAGS = frozenset({
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "div",
    "ul", "ol", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
})

_WS = re.compile(r"\s+")


def _walk(node):
    """Yield one string per block-level region under `node`, in document order."""
    from bs4 import NavigableString, Tag

    buf: list[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            buf.append(str(child))
        elif not isinstance(child, Tag):
            continue
        elif child.name in _BLOCK_TAGS:
            # A block ends whatever text preceded it — a list item's own
            # wording ahead of its nested list, a cell's ahead of a nested
            # paragraph. Gluing them invents word boundaries the diff then
            # reports as edits.
            yield "".join(buf)
            buf = []
            yield from _walk(child)
        else:
            # Inline. get_text() with NO separator, so "the <b>subject</b>
            # matter" neither loses nor gains a space.
            buf.append("\n" if child.name == "br" else child.get_text())
    yield "".join(buf)


def _clean_blocks(node) -> list[str]:
    return [b for b in (_WS.sub(" ", raw).strip() for raw in _walk(node)) if b]


def html_to_text(html: str) -> str:
    """Plain text of import HTML — the server-side stand-in for the editor's
    ``$getRoot().getTextContent()``.

    Every block ends a line and inline markup does not, which is what Lexical's
    own projection does. Byte-parity with it is not reachable from Python (nor
    needed): the consumer is `comparison_service.build_diff`, which aligns
    whitespace-insensitive word tokens, so what has to match is the WORDS and
    their order.
    """
    from bs4 import BeautifulSoup

    return "\n\n".join(_clean_blocks(BeautifulSoup(html or "", "html.parser")))


def html_to_blocks(html: str) -> list[dict]:
    """``[{tag, text}]`` — one entry per TOP-LEVEL element, the blocks the
    editor gets.

    ``$generateNodesFromDOM`` maps each top-level element of this fragment onto
    one root child: a ``<p>`` to a paragraph, an ``<h2>`` to a heading, a whole
    ``<ul>`` to one list node, a whole ``<table>`` to one table node. So the
    unit here is the top-level element, and everything nested inside it is that
    block's text — Lexical joins nested blocks with newlines, which
    ``lexical_anchor.normalize`` collapses to the single spaces used here.

    Empty blocks are dropped, exactly as ``SectionIdPlugin.readBlocks`` drops
    them: a blank paragraph is not something a finding can be anchored to, and
    the ordinals that disambiguate repeated blocks are assigned over the
    surviving list on both sides.
    """
    from bs4 import BeautifulSoup, Tag

    soup = BeautifulSoup(html or "", "html.parser")
    blocks: list[dict] = []
    for child in soup.children:
        if not isinstance(child, Tag):
            continue
        text = " ".join(_clean_blocks(child))
        if text:
            blocks.append({"tag": child.name, "text": text})
    return blocks
