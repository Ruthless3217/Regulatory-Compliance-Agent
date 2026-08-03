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
    return result.value
