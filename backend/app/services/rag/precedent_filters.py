"""Shared substance filter for precedent comments.

Single source of truth for "does this reviewer comment carry compliance signal
as a precedent example?" — used by both ingestion (don't store noise) and
retrieval (don't surface noise). Mirrors the 0007 corpus-purge denylist.
"""
from __future__ import annotations

import re
from typing import Optional

RESPONSE_TOKENS = frozenset({
 "done", "ok", "okay", "yes", "no",
 "noted", "agreed", "agree", "fine", "accepted", "approved", "confirmed",
 "added", "edited", "deleted", "revised", "rephrased", "checked", "check",
})

_NOISE_STATUS = {
 "new content", "new content added", "newly added", "changed", "corrected",
 "removed", "updated", "same comment as above",
 "this is already approved hence not rephrasing",
}
_NOISE_EDITORIAL = {
 "rephrase", "pls rephrase", "rephrase this", "pls rephrase this",
 "what do we mean by this", "what does this mean", "what is this", "what",
 "how", "pls elaborate", "pls elaborate how", "grammar check", "full form",
}
_NOISE_ROUTING = {
 "tax team to vet", "tax team approval",
 "pls take this ahead basis marketing pd approval",
 "pls take this ahead basis tax team approval",
}
_NOISE_SOURCE_REQUEST = {
 "source", "request source", "request source link", "pls add source",
 "pls add source link", "pls give us source", "pls attach source link",
 "attach source link", "pls help us locate", "pls help us locate in source link",
 "pls help us locate this in the source link", "pls help us locate this in source link",
 "pls help us locate in the source link", "pls align basis source link",
 "made changes basis source link", "where have we taken this from",
 "where is this in the source link",
}
NOISE_PHRASES = frozenset(
 _NOISE_STATUS | _NOISE_EDITORIAL | _NOISE_ROUTING | _NOISE_SOURCE_REQUEST
)

_WS_RE = re.compile(r"\s+")
_STRIP_CHARS = " .,!?;:\"''"


def normalize_comment(text: Optional[str]) -> str:
 c = _WS_RE.sub(" ", (text or "").strip().lower())
 return c.strip(_STRIP_CHARS)


def is_thin_comment(comment_text: Optional[str]) -> bool:
 """True when a comment carries no actionable signal as a precedent example:
 a pure-response token (done/ok/added/…), shorter than 3 chars, or
 high-frequency reviewer chatter (status/editorial/routing/generic source)."""
 raw = (comment_text or "").strip().lower().rstrip(".!?")
 if raw in RESPONSE_TOKENS or len(raw) < 3:
 return True
 return normalize_comment(comment_text) in NOISE_PHRASES
