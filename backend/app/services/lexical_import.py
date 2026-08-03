"""DOCX -> HTML, the import half of the Lexical working document.

The uploaded file is immutable, so this runs once at upload and its output is
converted to a Lexical state that the reviewer edits from then on. Fidelity
lost here is lost permanently, which is why the tests assert on structure
(headings, lists, tables) rather than on text.

mammoth is used rather than a hand-rolled OOXML walk: it already maps Word
styles onto semantic HTML, and the reverse direction (lexical_export) consumes
the same vocabulary.
"""
from __future__ import annotations

import io
import logging

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
    return result.value + _peripheral_html(docx_bytes)


def _peripheral_html(docx_bytes: bytes) -> str:
    """Headers, footers and text boxes, which mammoth does not read.

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

    blocks: list[str] = []
    for label, region in _iter_peripheral_regions(doc):
        lines = [p.text.strip() for p in region.paragraphs if p.text.strip()]
        for table in getattr(region, "tables", []):
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    lines.append(" | ".join(cells))
        if lines:
            blocks.append(
                f"<h3>{label}</h3>" + "".join(f"<p>{_escape(l)}</p>" for l in lines)
            )
    return "".join(blocks)


def _iter_peripheral_regions(doc):
    """(label, region) for every distinct header/footer in the document.

    Word gives each section its own header and footer, and an unlinked section
    can carry different wording — a different disclaimer on a different page.
    Deduplicated by text so an unchanged repeat is not emitted once per section.
    """
    seen: set[str] = set()
    for i, section in enumerate(doc.sections, start=1):
        for kind, region in (("header", section.header), ("footer", section.footer)):
            try:
                signature = "\n".join(p.text for p in region.paragraphs)
            except Exception:  # noqa: BLE001 — a malformed part must not abort the rest
                continue
            if not signature.strip() or signature in seen:
                continue
            seen.add(signature)
            suffix = "" if len(doc.sections) == 1 else f" {i}"
            yield f"Page {kind}{suffix}", region


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
