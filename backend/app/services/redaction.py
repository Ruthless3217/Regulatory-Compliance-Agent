"""PII redaction for the on-disk LLM interaction log.

`log.json` stores prompt/response excerpts of insurance marketing content, which
can carry customer PII (emails, phone numbers, PAN, policy/Aadhaar numbers).
:func:`redact_pii` masks the common identifiers before anything is written to
disk. It is a best-effort scrub (regex, not a full DLP) — the stronger control
is keeping file logging off in production (settings.llm_log_to_file).

Pure and synchronous so it is unit-tested directly.
"""
from __future__ import annotations

import re

# Order matters: emails and PAN are matched before the generic digit rule so
# their digits aren't masked piecemeal as [NUM].
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Indian PAN: 5 letters, 4 digits, 1 letter (e.g. ABCDE1234F).
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
# Phone: optional +91 / 0 prefix then 10 digits, allowing space/hyphen grouping.
_PHONE_RE = re.compile(r"(?<!\d)(?:\+91[\s-]?|0)?\d{5}[\s-]?\d{5}(?!\d)")
# Any remaining run of 8+ digits — policy / account / Aadhaar numbers.
_LONGNUM_RE = re.compile(r"\b\d{8,}\b")


def redact_pii(text: str) -> str:
    """Return ``text`` with emails, PAN, phone numbers, and long digit runs
    replaced by typed placeholders. Non-str input is returned unchanged."""
    if not isinstance(text, str) or not text:
        return text
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _PAN_RE.sub("[PAN]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    text = _LONGNUM_RE.sub("[NUM]", text)
    return text
