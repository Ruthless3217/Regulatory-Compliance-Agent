"""Read a whole document with the disclosure LLM backstop, instead of its head.

The backstop classifies which mandatory-disclaimer obligations a document carries.
It used to be handed ``document_text[:6000]``, so a paraphrased obligation in the
tail of a long brochure ("our equity fund returned 22% p.a." on page 20) was never
seen. Deterministic keyword triggers always read the full document — only LLM
paraphrase recall was truncated.

We slice the document into overlapping windows and fan them out concurrently,
unioning what each window reports. Cost is dominated by document tokens, which are
the same however the text is sliced; the window SIZE mainly sets the request count.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, List, Sequence, Tuple

logger = logging.getLogger(__name__)

# How far back from a hard cut we look for whitespace before accepting a mid-word
# split. A fraction of the window so it scales with the configured size.
_BACKOFF_FRACTION = 10


def window_text(text: str, size: int, overlap: int) -> List[str]:
    """Slice ``text`` into windows of at most ``size`` chars, overlapping by
    ``overlap``, preferring to break on whitespace.

    The overlap exists so an obligation phrase straddling a boundary still appears
    intact in the following window. Returns ``[]`` for blank text and ``[text]``
    when it already fits.
    """
    if size <= 0:
        raise ValueError("window size must be positive")
    if overlap < 0:
        raise ValueError("overlap must not be negative")
    if overlap >= size:
        raise ValueError("overlap must be smaller than the window size")

    t = text or ""
    if not t.strip():
        return []
    if len(t) <= size:
        return [t]

    backoff = max(1, size // _BACKOFF_FRACTION)
    windows: List[str] = []
    start, n = 0, len(t)
    while start < n:
        end = min(start + size, n)
        if end < n:
            # Walk back to the nearest whitespace so the window ends on a word
            # boundary; if there is none within the backoff budget, cut hard.
            floor = max(start + 1, end - backoff)
            cut = next((i for i in range(end, floor - 1, -1) if t[i - 1].isspace()), None)
            if cut is not None and cut > start:
                end = cut
        windows.append(t[start:end])
        if end >= n:
            break
        start = max(end - overlap, start + 1)  # max() guarantees forward progress
    return windows


async def classify_windows(
    windows: Sequence[str],
    obligation_types: Sequence[str],
    classify: Callable[[str, List[str]], Awaitable[List[str]]],
    *,
    max_concurrency: int = 4,
) -> Tuple[List[str], List[BaseException]]:
    """Run ``classify`` over ``windows`` in concurrent batches, unioning the
    obligation types each window reports. Returns ``(fired_types, failures)``.

    Windows are independent, so each batch runs concurrently and wall-clock stays
    close to a single round-trip rather than N of them. We stop as soon as every
    allowed type has fired — further windows cannot change the answer, which is
    what keeps a document that trips everything up front from paying for its whole
    length.

    A failed window is collected, not raised: the union only ever ADDS obligations,
    so results from the windows that succeeded remain valid. The caller decides how
    to report the incomplete coverage.
    """
    allowed = set(obligation_types)
    if not allowed:
        return [], []  # nothing to classify — a fan-out would buy nothing

    fired: set = set()
    failures: List[BaseException] = []
    stride = max(1, max_concurrency)

    for i in range(0, len(windows), stride):
        batch = windows[i : i + stride]
        results = await asyncio.gather(
            *(classify(w, list(obligation_types)) for w in batch),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, BaseException):
                failures.append(r)
                continue
            fired |= {t for t in (r or []) if t in allowed}
        if allowed and fired == allowed:
            break  # nothing left to learn

    return sorted(fired), failures
