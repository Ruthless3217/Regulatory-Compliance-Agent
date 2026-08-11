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
import re
from typing import Iterable, List

_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Collapse whitespace runs to one space, strip the ends, lowercase.

    THE definition, shared by fingerprints and block ids — and byte-for-byte
    the same rule as the editor's ``normalize()`` in
    ``frontend/components/editor/sectionMap.ts``. Both sides must agree or
    every id and every fingerprint misses.
    """
    return _WS.sub(" ", text or "").strip().lower()


def block_id(text: str, ordinal: int = 0) -> str:
    """The editor's public identity for one top-level block.

    sha1 of the normalized text, first 12 hex chars, with ``~n`` appended for
    the n-th repeat of an identical block. Mirrors ``sectionMap.blockId``; the
    frontend checks its hand-rolled SHA-1 against node's crypto, which is what
    licenses computing the same id here with hashlib.
    """
    digest = hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()[:12]
    return f"{digest}~{ordinal}" if ordinal > 0 else digest


def block_ids(texts: Iterable[str]) -> List[str]:
    """Ids for a document's blocks, in order.

    Mirrors ``nextSectionMap``'s mint path: identical blocks step the ordinal
    until the id is free, so three "Terms and conditions apply." paragraphs get
    three distinct ids in the same order on both sides.
    """
    used: set = set()
    out: List[str] = []
    for text in texts:
        ordinal = 0
        bid = block_id(text)
        while bid in used:
            ordinal += 1
            bid = block_id(text, ordinal)
        used.add(bid)
        out.append(bid)
    return out


def fingerprint(text: str) -> str:
    """Short, process-stable hex digest of `text`.

    Normalization: collapse every run of whitespace to a single space, strip
    the ends, lowercase. Both survive the edits that don't change what the
    passage says — a reflow, a re-indent, a sentence-case tweak.
    """
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:32]


def compute_anchor_fingerprint(before: str, span: str, after: str) -> str:
    """Fingerprint a span together with its surrounding text.

    Including the surroundings means a span whose own wording was edited can
    still be relocated by its context, and that two identical spans elsewhere
    in the document don't collide.
    """
    return fingerprint(f"{before or ''}{span or ''}{after or ''}")
