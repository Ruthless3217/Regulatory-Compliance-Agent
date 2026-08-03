"""Bridges an uploaded submission to its Lexical working document.

Import is best-effort by design: a submission that cannot be converted keeps
working on extracted text exactly as before, because every pre-existing
submission is in that state and must stay usable.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from app.models.submission import Submission
from app.services.lexical_import import LexicalImportError, docx_to_html, pdf_to_html

logger = logging.getLogger(__name__)


def build_import_html(submission: Submission) -> Optional[str]:
    """HTML to seed this submission's editor, or None if it has no importable
    source. Reads the upload; never writes it.

    A PDF is reconstructed rather than read — the words survive, the layout does
    not, and its export will not look like the original. See the fidelity note
    in ``lexical_import``.
    """
    if submission.content_type not in ("docx", "pdf") or not submission.file_path:
        return None
    if not os.path.exists(submission.file_path):
        logger.warning("lexical import: upload missing for %s", submission.id)
        return None
    try:
        with open(submission.file_path, "rb") as f:
            data = f.read()
        if submission.content_type == "docx":
            return docx_to_html(data)
        return pdf_to_html(data)
    except LexicalImportError as exc:
        logger.warning("lexical import failed for %s: %s", submission.id, exc)
        return None
