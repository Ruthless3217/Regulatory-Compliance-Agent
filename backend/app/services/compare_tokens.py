"""Comparison tokens for the content-first Compare engine.

Two representations are kept apart:
  * source words (``PositionedWord``) — original text and boxes, never modified;
  * comparison tokens (``CompToken``) — a normalised ``key`` used only for matching,
    plus ``src`` (indices of the source words it stands for) and the original ``text``.

Stage A — ``comparison_key``: conservative normalisation. Equivalent *encodings* of the
same character share a key (NFKC ligatures, curly vs straight quotes, dash variants,
soft hyphen / zero-width / NBSP); matching is case-insensitive and ignores surrounding
punctuation. Content characters are kept: digits, internal punctuation, %, currency
symbols (₹ is content, not punctuation) and a trailing hyphen (an unresolved split must
not silently equal the bare stem).

Stage B — ``reconstruct_words``: rejoin words a layout split apart, only with layout
evidence AND textual evidence from the other document:
  * line-end hyphen: ``Per-`` last on its line + ``manent`` first on the next line of the
    same block. Joined as ``permanent`` — or ``per-manent`` if only the hyphenated form
    exists on the other side (a lexical hyphen). Never for numbers/identifiers.
  * line-break split without hyphen (``Relationshi`` / ``p``) and tiny-gap box splits
    (OCR / justification) — joined only if the concatenation is a word the other
    document uses and the parts are not both words it uses (``may be`` vs ``maybe``
    stays a real change). Same-line words separated by a normal space never merge.
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from app.services.comparison_service import _is_placeholder

PLACEHOLDER_KEY = "\x00ph"

_TRANSLATE = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ",
}
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)
# stripped from the edges only; % ₹ $ € £ # & @ / are content and stay
_EDGE = "\"'()[]{}<>.,;:!?*•·…|`"
_LEADER = re.compile(r"[.…]{3,}")   # dotted leader = a fill-in blank, like ______


def _clean(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").translate(_INVISIBLE)
    return "".join(_TRANSLATE.get(c, c) for c in t)


def comparison_key(text: str) -> str:
    """Normalised matching key for one word (see module doc)."""
    if _is_placeholder(text) or _LEADER.fullmatch((text or "").strip()):
        return PLACEHOLDER_KEY
    t = _clean(text).strip()
    core = t.strip(_EDGE).lstrip("-")
    if not core:
        return t.casefold()
    return core.casefold()


@dataclass
class CompToken:
    key: str
    text: str                      # original source text (joined with no separator when rebuilt)
    src: Tuple[int, ...]           # indices into the source word list
    page: int = 0
    block_id: int = 0
    line_id: int = 0
    reason: Optional[str] = None   # "hyphen-join" | "split-join" | None
    display: Optional[str] = None  # readable joined form (e.g. "permanent" for "Per-"+"manent")
    placeholder: bool = False      # inside a multi-word template field "<Name of the Policyholder>"


def build_tokens(words: Sequence) -> List[CompToken]:
    """Stage A: one token per source word."""
    return [
        CompToken(key=comparison_key(w.text), text=w.text, src=(i,), page=w.page,
                  block_id=getattr(w, "block_id", 0), line_id=getattr(w, "line_id", 0))
        for i, w in enumerate(words)
    ]


# ---------------------------------------------------------------- Stage B
_HYPHEN_STEM = re.compile(r"^[^\W\d_](?:[^\W\d_]|['.])*-$")   # letters only, ends with '-'
_ALPHA_START = re.compile(r"^[^\W\d_]")


def _line_break(a, b) -> bool:
    """b continues a on the next line of the same block — or b is the first word of the
    next page after a, the last word of its page (text flowing over a page break)."""
    if b.page == a.page + 1:
        return True
    return (getattr(a, "block_id", 0) == getattr(b, "block_id", 0)
            and getattr(b, "line_id", 0) == getattr(a, "line_id", 0) + 1)


def _tiny_gap(a, b) -> bool:
    if getattr(a, "line_id", 0) != getattr(b, "line_id", 0) or a.page != b.page:
        return False
    char_w = (a.x1 - a.x0) / max(1, len(a.text))
    return 0 <= b.x0 - a.x1 < 0.25 * char_w


def _case_ok(stem: str, nxt: str) -> bool:
    """Continuation must look like the rest of a word: lowercase, or ALL-CAPS after ALL-CAPS."""
    first = nxt[0]
    return first.islower() or (stem.isupper() and nxt.rstrip(",.;:)").isupper())


def _hyphen_join(a, b) -> Optional[Tuple[str, str]]:
    """(plain, hyphenated) readings if a/b is a layout line-end hyphen split."""
    if not (_HYPHEN_STEM.match(a.text) and _ALPHA_START.match(b.text) and _line_break(a, b)):
        return None
    if not _case_ok(a.text, b.text):
        return None
    stem = a.text[:-1]
    return stem + b.text, a.text + b.text


def vocabulary(tokens: Iterable[CompToken], words: Optional[Sequence] = None) -> Set[str]:
    """Keys a document uses, plus both readings of its line-end hyphen splits."""
    toks = list(tokens)
    v = {t.key for t in toks}
    for a, b in zip(toks, toks[1:]):
        if len(a.src) == 1 and len(b.src) == 1 and a.text.endswith("-") and b.line_id == a.line_id + 1 \
                and a.block_id == b.block_id and _HYPHEN_STEM.match(a.text) and _ALPHA_START.match(b.text):
            v.add(comparison_key(a.text[:-1] + b.text))
            v.add(comparison_key(a.text + b.text))
    return v


def reconstruct_words(words: Sequence, other_vocab: Set[str]) -> List[CompToken]:
    """Stage B: tokens with justified word reconstruction (see module doc)."""
    out: List[CompToken] = []
    i, n = 0, len(words)
    while i < n:
        a = words[i]
        # chain line-end hyphen joins: Criti- / cal  |  Ill- / ness
        if i + 1 < n:
            if _hyphen_join(a, words[i + 1]):
                # a word may continue over several lines: BHU- / BANESH- / WAR
                plain, hyph, j = a.text[:-1], a.text, i + 1
                while j + 1 < n and words[j].text.endswith("-") and _hyphen_join(words[j], words[j + 1]):
                    plain += words[j].text[:-1]; hyph += words[j].text; j += 1
                plain += words[j].text; hyph += words[j].text
                pk, hk = comparison_key(plain), comparison_key(hyph)
                use_hyph = hk in other_vocab and pk not in other_vocab
                out.append(CompToken(key=hk if use_hyph else pk, text=hyph, src=tuple(range(i, j + 1)), page=a.page,
                                     block_id=getattr(a, "block_id", 0), line_id=getattr(a, "line_id", 0),
                                     reason="hyphen-join", display=hyph if use_hyph else plain))
                i = j + 1
                continue
            b = words[i + 1]
            if _line_break(a, b) or _tiny_gap(a, b):
                joined = _evidence_join(a.text, b.text, other_vocab)
                if joined is not None:
                    key, shown = joined
                    out.append(CompToken(key=key, text=a.text + b.text, src=(i, i + 1), page=a.page,
                                         block_id=getattr(a, "block_id", 0), line_id=getattr(a, "line_id", 0),
                                         reason="hyphen-join" if a.text.endswith("-") else "split-join",
                                         display=shown))
                    i += 2
                    continue
        out.append(CompToken(key=comparison_key(a.text), text=a.text, src=(i,), page=a.page,
                             block_id=getattr(a, "block_id", 0), line_id=getattr(a, "line_id", 0)))
        i += 1
    return out


def _is_word(key: str) -> bool:
    """A real word for the 'may be'/'maybe' guard: at least two letters ('s)' is not)."""
    return sum(c.isalpha() for c in key) >= 2


def _evidence_join(a: str, b: str, other_vocab: Set[str]) -> Optional[Tuple[str, str]]:
    """Key of the joined word if the other document uses exactly that word.

    Layout evidence (line break / tiny box gap) is checked by the caller. Candidates:
    the exact concatenation (keeps a lexical hyphen, slash, '.', '@' — 'Non-'+'Forfeiture',
    'Authority/'+'IRDAI', 'thane@'+'cioins.co.in', '0612-'+'2547068'); and, for a
    letters-only stem ending in '-', the de-hyphenated form ('Email:bi-'+'malokpal…').
    A number never loses its hyphen. If both halves are themselves words the other
    side uses ('may'/'be' vs 'maybe') it is a wording difference, not a split.
    """
    ak, bk = comparison_key(a), comparison_key(b)
    if ak in other_vocab and bk in other_vocab and _is_word(ak) and _is_word(bk):
        return None
    candidates = [a + b]
    if a.endswith("-") and not any(c.isdigit() for c in a):
        candidates.append(a[:-1] + b)
    for text in candidates:
        k = comparison_key(text)
        # a join must make a new word — never just absorb a bullet/quote/bracket
        if k in other_vocab and k not in (ak, bk):
            return k, text
    return None
