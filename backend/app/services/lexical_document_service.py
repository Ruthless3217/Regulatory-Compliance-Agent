"""Bridges an uploaded submission to its Lexical working document.

Import is best-effort by design: a submission that cannot be converted keeps
working on extracted text exactly as before, because every pre-existing
submission is in that state and must stay usable.

Best-effort is not the same as silent. A failure is *reported* — see
``ImportResult`` — because an empty editor looks identical whether the document
imported fine and is blank, has no text layer to import, or hit a conversion
that broke. The reviewer cannot tell those apart by staring at the page.
"""
from __future__ import annotations

import logging
import os
from typing import NamedTuple, Optional

from app.models.submission import Submission
from app.services.lexical_import import LexicalImportError, docx_to_html, pdf_to_html

logger = logging.getLogger(__name__)

# The formats with an importable source. One list, so `can_import` can never
# answer yes to something `build_import_html` then refuses.
_IMPORTABLE_CONTENT_TYPES = ("docx", "pdf")


def can_import(submission: Submission) -> bool:
    """Whether an import is possible, WITHOUT doing it.

    `GET /submissions/{id}` needs to tell the editor there is something to seed
    from. It must not convert the document to find out: that is seconds of CPU
    for a long file, and answering a yes/no question by doing the work is how
    opening a submission started timing out.
    """
    return (
        submission.content_type in _IMPORTABLE_CONTENT_TYPES
        and bool(submission.file_path)
        and os.path.exists(submission.file_path)
    )


class ImportResult(NamedTuple):
    """One import attempt, and what to tell the reviewer about it.

    Three outcomes, because the editor must show a different thing for each:

      * ``imported``    — ``html`` is the seed; ``reason`` is None.
      * ``unavailable`` — there was nothing to import, and that is a property
                          of the document (no upload, no text layer, no text).
                          Not an app failure, and must not be phrased as one.
      * ``failed``      — the conversion broke; ``reason`` says how.

    ``reason`` is reviewer-facing prose, not a log line: it is rendered in the
    editor where the document would have been.
    """

    html: Optional[str]
    status: str
    reason: Optional[str]


def import_html(submission: Submission) -> ImportResult:
    """HTML to seed this submission's editor — or why there is none. Reads the
    upload; never writes it.

    A PDF is reconstructed rather than read — the words survive, the layout does
    not, and its export will not look like the original. See the fidelity note
    in ``lexical_import``.
    """
    if submission.content_type not in _IMPORTABLE_CONTENT_TYPES:
        return ImportResult(
            None,
            "unavailable",
            f"A {submission.content_type or 'plain text'} submission has no document "
            "to import — only DOCX and PDF uploads can seed the editor.",
        )
    if not submission.file_path or not os.path.exists(submission.file_path):
        logger.warning("lexical import: upload missing for %s", submission.id)
        return ImportResult(
            None,
            "unavailable",
            "The uploaded file is no longer on the server, so it cannot be imported.",
        )
    try:
        with open(submission.file_path, "rb") as f:
            data = f.read()
        html = docx_to_html(data) if submission.content_type == "docx" else pdf_to_html(data)
    except LexicalImportError as exc:
        logger.warning("lexical import failed for %s: %s", submission.id, exc)
        # A PDF with no text layer is not a malfunction: it is a scan or
        # outlined artwork, and there is genuinely nothing to edit. Saying
        # "conversion failed" there sends the reviewer chasing a bug that is
        # really a property of their document.
        # ponytail: told apart by the message lexical_import raises with; give
        # LexicalImportError a code if a second case ever needs telling apart.
        # A reworded message degrades to the generic failure, which is still
        # honest — just less specific.
        if "text layer" in str(exc):
            return ImportResult(
                None,
                "unavailable",
                "This PDF has no text layer — it is a scan or outlined artwork, so "
                "there is no text to edit. The findings were graded on text read by OCR.",
            )
        return ImportResult(None, "failed", str(exc))
    if not html.strip():
        # A conversion that succeeded onto nothing. Distinct from a failure,
        # and the reviewer still needs telling why the page is blank.
        return ImportResult(None, "unavailable", "The uploaded document contains no text.")
    return ImportResult(html, "imported", None)


def build_import_html(submission: Submission) -> Optional[str]:
    """HTML to seed this submission's editor, or None if there is none.

    The best-effort view of ``import_html``, kept for callers that only degrade
    to extracted text and have nothing to say about why. Anything shown to a
    reviewer should call ``import_html`` and render its ``reason``.
    """
    return import_html(submission).html
