"""Lightweight PII masking for logs / traces.

Masks the common Indian-context identifiers that can appear in marketing
submissions or chat: email, phone, PAN, Aadhaar, and long card-like digit runs.
Used to scrub LLM interaction logs so confidential content + PII don't sit in
plaintext on disk. NOT applied to the analysis prompt itself — the model needs
the real copy to flag violations — only to what we persist for debugging.
"""
from __future__ import annotations

import re
from typing import Any

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Indian mobile / phone: optional +91, then a 10-digit number that may be split
# by a single space/hyphen (e.g. "98765 43210", "+91 98765 43210", "9876543210").
_PHONE = re.compile(r"(?:\+?91[\-\s]?)?\b[6-9]\d{4}[\-\s]?\d{5}\b")
_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_AADHAAR = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")


def mask_pii(text: Any) -> Any:
    """Return ``text`` with emails/phones/PAN/Aadhaar/card numbers masked.

    Non-string input is returned unchanged (callers may pass None / dicts)."""
    if not isinstance(text, str) or not text:
        return text
    text = _EMAIL.sub("[EMAIL]", text)
    text = _AADHAAR.sub("[AADHAAR]", text)
    text = _PAN.sub("[PAN]", text)
    text = _CARD.sub("[CARD]", text)
    text = _PHONE.sub("[PHONE]", text)
    return text


def mask_obj(obj: Any) -> Any:
    """Recursively mask PII in strings within dicts/lists (for context blobs)."""
    if isinstance(obj, dict):
        return {k: mask_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_obj(v) for v in obj]
    return mask_pii(obj)
