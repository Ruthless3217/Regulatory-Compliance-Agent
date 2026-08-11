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
from functools import lru_cache
from typing import NamedTuple, Optional

from app.models.submission import Submission
from app.services.lexical_import import (
    LexicalImportError,
    docx_to_html,
    html_to_blocks,
    html_to_text,
    pdf_to_html,
)

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


def import_text(submission: Submission) -> Optional[str]:
    """Plain text of the document the editor was SEEDED with, or None when this
    submission has nothing importable.

    The baseline a working copy has to be diffed against. The working copy is
    this text plus the reviewer's edits — Lexical persists
    ``$getRoot().getTextContent()`` as the revision's content — so diffing it
    against the analyser's own extraction of the same upload instead reports
    every disagreement between the two readers (text boxes, "[PAGE FOOTER]"
    labels, table pipes, PDF page chrome) as a reviewer edit.

    Costs a whole conversion, so callers must keep it off the event loop.
    """
    html = _seed_html(submission)
    return html_to_text(html) or None if html else None


def import_blocks(submission: Submission) -> Optional[list]:
    """``[{tag, text}]`` for the document the editor was SEEDED with, or None.

    The same blocks ``$generateNodesFromDOM`` gives the editor, so an id minted
    from one of them here names a block that exists on the other side. See
    ``lexical_import.html_to_blocks``.

    Costs a whole conversion on a cache miss, so callers must keep it off the
    event loop.
    """
    html = _seed_html(submission)
    return html_to_blocks(html) or None if html else None


def document_blocks(submission: Submission) -> Optional[list]:
    """The blocks the editor holds RIGHT NOW, or None for a submission whose
    editor document is not a rich import.

    ``lexical_html`` is the reviewer's saved working document — once it exists
    it, not the upload, is what the editor shows and what a finding has to be
    anchored into. Falling back to the import for a submission that has been
    edited would mint ids for blocks the editor no longer has.

    Restricted to DOCX/PDF on purpose. A pasted HTML or plain-text submission is
    graded on extracted text the editor never held — surfaced meta tags, the
    ``[PAGE FOOTER]`` labels — so chunking it by blocks would change WHAT is
    graded rather than only how it is cut.
    """
    if submission.content_type not in _IMPORTABLE_CONTENT_TYPES:
        return None
    if getattr(submission, "lexical_html", None):
        return html_to_blocks(submission.lexical_html) or None
    return import_blocks(submission)


def _seed_html(submission: Submission) -> Optional[str]:
    if not can_import(submission):
        return None
    stat = os.stat(submission.file_path)
    return _import_html(
        submission.file_path, submission.content_type, stat.st_mtime_ns, stat.st_size
    )


@lru_cache(maxsize=16)
def _import_html(
    file_path: str, content_type: str, mtime_ns: int, size: int
) -> Optional[str]:
    """Cached: the redline is re-fetched every time the reviewer opens Split,
    and converting a long DOCX costs seconds. The upload is immutable, so the
    only invalidation needed is a path being reused for a different file —
    which mtime and size carry.
    """
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        return docx_to_html(data) if content_type == "docx" else pdf_to_html(data)
    except (LexicalImportError, OSError) as exc:
        # No baseline of this lineage; the caller falls back to extracted text,
        # which is also what the editor fell back to for this submission.
        logger.warning("lexical baseline unavailable for %s: %s", file_path, exc)
        return None


def build_import_html(submission: Submission) -> Optional[str]:
    """HTML to seed this submission's editor, or None if there is none.

    The best-effort view of ``import_html``, kept for callers that only degrade
    to extracted text and have nothing to say about why. Anything shown to a
    reviewer should call ``import_html`` and render its ``reason``.
    """
    return import_html(submission).html
