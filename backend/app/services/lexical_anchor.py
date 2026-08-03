"""Text fingerprints for relocating a violation inside a Lexical document.

A violation's anchor is (node key, offset_start, offset_end). Lexical re-keys
nodes freely — on reload, on paste, on any structural edit — so the key alone
is not durable. The fingerprint is the fallback: hash the span together with
its surroundings, and a re-keyed span can still be found by scanning the tree
for matching text.

hashlib, deliberately, not `hash()`: the builtin is salted per interpreter
(PYTHONHASHSEED), so the API process writing the anchor and the one reading it
back would compute different values for the same text.
"""
import hashlib


def fingerprint(text: str) -> str:
    """Short, process-stable hex digest of `text`.

    Normalization: collapse every run of whitespace to a single space, strip
    the ends, lowercase. Both survive the edits that don't change what the
    passage says — a reflow, a re-indent, a sentence-case tweak.
    """
    normalized = " ".join((text or "").split()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def compute_anchor_fingerprint(before: str, span: str, after: str) -> str:
    """Fingerprint a span together with its surrounding text.

    Including the surroundings means a span whose own wording was edited can
    still be relocated by its context, and that two identical spans elsewhere
    in the document don't collide.
    """
    return fingerprint(f"{before or ''}{span or ''}{after or ''}")
