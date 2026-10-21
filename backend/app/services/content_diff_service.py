"""Content-first comparison engine for the Compare tool.

Rule: a highlight must be justified by a content difference — never by the structure
used to display the content. Structure (pages, columns, table cells, paragraph breaks)
only orders the words; whether a change exists is decided on the token stream alone.

Pipeline (both documents):
  source words (reading order, boxes)        <- pdf_render_service.positioned_words
  -> comparison tokens + reconstruction      <- compare_tokens (Stage A/B)
  -> glued list markers split, multi-token <placeholders> collapsed
  -> one global alignment of token keys (difflib longest-matching-block recursion: it
     anchors on long verbatim runs first, which keeps repeated boilerplate attached to
     the right occurrence; measured 0.964 token equality on the eTouch II pair)
  -> change records, each with a category:
       content       wording/number/identifier/amount changes (always visible)
       typographic   case, quote style, punctuation, spacing, currency symbol (visible)
       placeholder   template field <..>/____ vs blank or filled value
       renumbering   list markers only, e.g. 15. -> 14.
       layout        table header row repeated on a continuation page, and running
                     headers/footers (compared page against page, ``compare_furniture``)
     plus moves: identical (or >=90% identical) text relocated is a move, with any
     inner edit reported as its own content change.

Both views are produced from the same records: ``to_overlay`` (pixel boxes) and
``to_blocks`` (text redline DiffBlocks), so they cannot disagree.
"""
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Set, Tuple

from app.services.compare_tokens import (
    PLACEHOLDER_KEY,
    CompToken,
    _clean,
    build_tokens,
    comparison_key,
    reconstruct_words,
    vocabulary,
)
from app.services.comparison_service import _ends_sentence, _is_placeholder

# Filter groups the viewer offers. Like Draftable, every detected difference is shown
# by default (case, punctuation, placeholders, running headers/footers included); the
# groups only let the reviewer switch categories off. The substantive subset is kept
# for callers that want wording changes only.
SUBSTANTIVE_GROUPS = {"insertions", "deletions", "replacements", "moves", "currency"}
ALL_GROUPS = SUBSTANTIVE_GROUPS | {"case", "punctuation", "quotes", "spacing",
                                   "placeholders", "renumbering", "layout", "headers_footers"}
DEFAULT_GROUPS = set(ALL_GROUPS)
_TYPO_GROUP = {"case": "case", "punctuation": "punctuation", "quote": "quotes",
               "spacing": "spacing", "currency": "currency"}

_MARKER = re.compile(r"^\(?(?:\d{1,3}|[ivxlc]{1,6}|[a-z])[.)]$", re.IGNORECASE)
_GLUED_MARKER = re.compile(r"^(\(?(?:\d{1,3}|[ivxlc]{1,6})[.)])([A-Za-z“\"'(].*)$", re.IGNORECASE)
_SENT_END = (".", ";", ":", "?", "!")
_MOVE_MIN_TOKENS = 3
_MOVE_MIN_CHARS = 12          # a single long token (e-mail, URL) can move on its own
_NEAR_MOVE_MIN_TOKENS = 8
_NEAR_MOVE_RATIO = 0.9


@dataclass
class Change:
    id: str
    kind: str                       # added | removed | modified | moved
    category: str                   # content | typographic | placeholder | renumbering | layout
    subtype: Optional[str] = None   # typographic: case | quote | punctuation | spacing | currency
    old_tok: List[int] = field(default_factory=list)
    new_tok: List[int] = field(default_factory=list)
    old_words: List[int] = field(default_factory=list)   # source word indices
    new_words: List[int] = field(default_factory=list)
    old_text: str = ""
    new_text: str = ""
    move_id: Optional[str] = None
    change_type: str = ""
    metadata: Dict = field(default_factory=dict)
    filter_group: str = ""
    # insertion/deletion point on the side that has no text: (source word index, before|after)
    old_anchor: Optional[Tuple[int, str]] = None
    new_anchor: Optional[Tuple[int, str]] = None

    @property
    def default_visible(self) -> bool:
        return self.filter_group in DEFAULT_GROUPS

    @property
    def visible(self) -> bool:  # default view
        return self.default_visible


@dataclass
class ContentDiff:
    old_words: Sequence
    new_words: Sequence
    old_tokens: List[CompToken]
    new_tokens: List[CompToken]
    changes: List[Change]


# ---------------------------------------------------------------- token preparation
def _display(t: CompToken) -> str:
    """Readable text of a token: a layout hyphen join shows the plain word."""
    return t.display or t.text


def _split_glued_markers(tokens: List[CompToken]) -> List[CompToken]:
    """'15.If' (marker glued to the word by the renderer) -> '15.' + 'If'."""
    out: List[CompToken] = []
    for t in tokens:
        m = _GLUED_MARKER.match(t.text) if len(t.src) == 1 else None
        if m:
            for part in (m.group(1), m.group(2)):
                out.append(CompToken(key=comparison_key(part), text=part, src=t.src, page=t.page,
                                     block_id=t.block_id, line_id=t.line_id))
        else:
            out.append(t)
    return out


_EMBEDDED_PH = re.compile(r"<[^<>\s]{1,40}>")


def _split_glued_placeholders(tokens: List[CompToken]) -> List[CompToken]:
    """'Date<dd/mm/yyyy>Variant' (cells rendered touching) -> 'Date', '<dd/mm/yyyy>', 'Variant'."""
    out: List[CompToken] = []
    for t in tokens:
        m = _EMBEDDED_PH.search(t.text) if len(t.src) == 1 else None
        if not m or (m.start() == 0 and m.end() == len(t.text)):
            out.append(t)
            continue
        rest = t.text
        while rest:
            m = _EMBEDDED_PH.search(rest)
            parts = [rest] if not m else [rest[:m.start()], m.group(0)]
            for part in parts:
                if part:
                    out.append(CompToken(key=comparison_key(part), text=part, src=t.src, page=t.page,
                                         block_id=t.block_id, line_id=t.line_id))
            rest = "" if not m else rest[m.end():]
    return out


def _mark_placeholder_spans(tokens: List[CompToken]) -> List[CompToken]:
    """Flag the words of a multi-word template field ('<Name of the Policyholder>',
    '< M / F/ T >'). They keep their own keys, so a descriptive label still matches the
    same label on the other side; whatever differs inside the field is a placeholder."""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.text.startswith("<") and ">" not in t.text:
            j = i + 1
            while j < len(tokens) and j - i <= 15 and ">" not in tokens[j].text and "<" not in tokens[j].text:
                j += 1
            if j < len(tokens) and j - i <= 15 and ">" in tokens[j].text:
                for k in range(i, j + 1):
                    tokens[k].placeholder = True
                i = j + 1
                continue
        i += 1
    return tokens


def _prepare(words: Sequence, other_words: Sequence) -> List[CompToken]:
    other_vocab = vocabulary(build_tokens(other_words))
    toks = reconstruct_words(words, other_vocab)
    return _mark_placeholder_spans(_split_glued_placeholders(_split_glued_markers(toks)))


# ---------------------------------------------------------------- classification helpers
def _is_ph(t: CompToken) -> bool:
    return t.key == PLACEHOLDER_KEY or t.placeholder


_PUNCT_ONLY = re.compile(r"^[^\w₹%]+$")


def _is_marker(tokens: List[CompToken], i: int) -> bool:
    t = tokens[i]
    if not _MARKER.match(t.text.strip()):
        return False
    if i == 0:
        return True
    prev = tokens[i - 1]
    return prev.line_id != t.line_id or prev.text.rstrip("\"'”)").endswith(_SENT_END)


def _currency_norm(keys: List[str]) -> List[str]:
    out = []
    for k in keys:
        k = re.sub(r"^rs\.?$", "₹", k)
        k = k.replace("₹", "")
        if k:
            out.append(k)
    return out


def _typographic_subtype(a: CompToken, b: CompToken) -> Optional[str]:
    """For tokens with equal keys, the kind of surface difference (None if identical)."""
    if a.text == b.text or len(a.src) != 1 or len(b.src) != 1:
        return None
    ca, cb = _clean(a.text), _clean(b.text)
    if ca == cb:
        return "quote"
    if ca.casefold() == cb.casefold():
        return "case"
    return "punctuation"


# ---------------------------------------------------------------- the engine
def compare_words(old_words: Sequence, new_words: Sequence) -> ContentDiff:
    ot = _prepare(old_words, new_words)
    nt = _prepare(new_words, old_words)
    ok, nk = [t.key for t in ot], [t.key for t in nt]
    ops = SequenceMatcher(None, ok, nk, autojunk=False).get_opcodes()

    raw: List[Change] = []

    def mk(kind, category, i_idx, j_idx, subtype=None):
        raw.append(Change(id="", kind=kind, category=category, subtype=subtype,
                          old_tok=list(i_idx), new_tok=list(j_idx)))

    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            run = None
            for a, b in zip(range(i1, i2), range(j1, j2)):
                if _is_ph(ot[a]) or _is_ph(nt[b]):
                    if ot[a].text != nt[b].text:          # '<dd/mm/yyyy>' vs '______', '<Name' vs 'Name'
                        mk("modified", "placeholder", [a], [b])
                    continue
                st = _typographic_subtype(ot[a], nt[b])
                if st and run and run[0] == st and run[1][-1] == a - 1:
                    run[1].append(a); run[2].append(b)
                    continue
                if run:
                    mk("modified", "typographic", run[1], run[2], run[0]); run = None
                if st:
                    run = (st, [a], [b])
            if run:
                mk("modified", "typographic", run[1], run[2], run[0])
            continue
        _classify_op(ot, nt, list(range(i1, i2)), list(range(j1, j2)), mk)

    _detect_moves(ot, nt, raw)
    _detect_repeated_headers(nt, raw)
    return _finalise(old_words, new_words, ot, nt, raw, ops)


def _classify_op(ot, nt, ii: List[int], jj: List[int], mk) -> None:
    """Split one non-equal alignment op into categorised changes."""
    # list markers at the edges of the op are numbering, not wording
    lead_o = [i for i in ii if _is_marker(ot, i)]
    lead_n = [j for j in jj if _is_marker(nt, j)]
    core_o = [i for i in ii if i not in lead_o]
    core_n = [j for j in jj if j not in lead_n]
    if lead_o or lead_n:
        mk("modified" if lead_o and lead_n else ("removed" if lead_o else "added"), "renumbering", lead_o, lead_n)
        ii, jj = core_o, core_n
        if not ii and not jj:
            return

    ph_o = [i for i in ii if _is_ph(ot[i])]
    ph_n = [j for j in jj if _is_ph(nt[j])]
    # pure fill: a template field on one side, its value (or nothing) on the other
    if (ii and len(ph_o) == len(ii) and not ph_n) or (jj and len(ph_n) == len(jj) and not ph_o):
        mk("modified" if ii and jj else ("removed" if ii else "added"), "placeholder", ii, jj)
        return
    if ph_o or ph_n:
        mk("removed" if ph_o and not ph_n else ("added" if ph_n and not ph_o else "modified"), "placeholder", ph_o, ph_n)
        ii = [i for i in ii if i not in ph_o]
        jj = [j for j in jj if j not in ph_n]

    if not ii and not jj:
        return
    ko = [ot[i].key for i in ii]
    kn = [nt[j].key for j in jj]
    kind = "modified" if ii and jj else ("removed" if ii else "added")
    if all(_PUNCT_ONLY.match(ot[i].text) for i in ii) and all(_PUNCT_ONLY.match(nt[j].text) for j in jj):
        mk(kind, "typographic", ii, jj, "punctuation")          # 'PART - A' vs 'Part A'
    elif ii and jj and "".join(ko) == "".join(kn):
        mk("modified", "typographic", ii, jj, "spacing")
    elif _currency_norm(ko) == _currency_norm(kn):
        mk(kind, "typographic", ii, jj, "currency")
    elif ii and jj and re.sub(r"[^\w₹%]", "", "".join(ko)) == re.sub(r"[^\w₹%]", "", "".join(kn)):
        mk("modified", "typographic", ii, jj, "punctuation")
    else:
        mk(kind, "content", ii, jj)


def _tok_chars(tokens, idx) -> int:
    return sum(len(tokens[i].key) for i in idx)


def _detect_moves(ot, nt, raw: List[Change]) -> None:
    """Pair removed/added content with identical (or >=90% identical) text elsewhere."""
    from collections import Counter
    count_o = Counter(t.key for t in ot)
    count_n = Counter(t.key for t in nt)
    removed = [c for c in raw if c.category == "content" and c.kind == "removed"]
    added = [c for c in raw if c.category == "content" and c.kind == "added"]
    used: Set[int] = set()
    n_move = 0
    for r in removed:
        rk = [ot[i].key for i in r.old_tok]
        best = None
        for a_i, a in enumerate(added):
            if a_i in used:
                continue
            ak = [nt[j].key for j in a.new_tok]
            # a long or document-unique single word ('Odisha' in another table cell) may move alone
            unique_word = len(rk) == 1 and len(rk[0]) >= 5 and count_o[rk[0]] == 1 and count_n[rk[0]] == 1
            if rk == ak and (len(rk) >= _MOVE_MIN_TOKENS or _tok_chars(ot, r.old_tok) >= _MOVE_MIN_CHARS
                             or unique_word):
                best = (1.0, a_i); break
            if min(len(rk), len(ak)) >= _NEAR_MOVE_MIN_TOKENS:
                ratio = SequenceMatcher(None, rk, ak, autojunk=False).ratio()
                if ratio >= _NEAR_MOVE_RATIO and (best is None or ratio > best[0]):
                    best = (ratio, a_i)
        if best is None:
            continue
        a = added[best[1]]
        used.add(best[1])
        mid = f"m{n_move}"; n_move += 1
        ak = [nt[j].key for j in a.new_tok]
        if best[0] == 1.0:
            r.kind = a.kind = "moved"; r.move_id = a.move_id = mid
            continue
        # near-identical move: matched parts move, the differences are their own edits
        sm = SequenceMatcher(None, rk, ak, autojunk=False)
        mo, mn = [], []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                mo += r.old_tok[i1:i2]; mn += a.new_tok[j1:j2]
            else:
                kind = "modified" if (i2 > i1 and j2 > j1) else ("removed" if i2 > i1 else "added")
                edit = Change(id="", kind=kind, category="content", old_tok=r.old_tok[i1:i2], new_tok=a.new_tok[j1:j2])
                edit.metadata["within_move"] = mid
                raw.append(edit)
        r.old_tok, a.new_tok = mo, mn
        r.kind = a.kind = "moved"; r.move_id = a.move_id = mid


def _detect_repeated_headers(nt, raw: List[Change]) -> None:
    """A table header row the renderer repeats at the top of a continuation page is layout."""
    keys = [t.key for t in nt]
    for c in raw:
        if c.category != "content" or c.kind != "added" or len(c.new_tok) < 4:
            continue
        seq = keys[c.new_tok[0]:c.new_tok[-1] + 1]
        first = nt[c.new_tok[0]]
        page_tokens = [k for k, t in enumerate(nt) if t.page == first.page]
        at_top = page_tokens and c.new_tok[0] - page_tokens[0] <= 3
        earlier = any(keys[s:s + len(seq)] == seq for s in range(0, c.new_tok[0] - len(seq) + 1))
        if at_top and earlier:
            c.category = "layout"; c.subtype = "repeated-header"


def _filter_group(c: Change) -> str:
    if c.category == "typographic":
        return _TYPO_GROUP.get(c.subtype or "", "punctuation")
    if c.category == "placeholder":
        return "placeholders"
    if c.category in ("renumbering", "layout"):
        return c.category
    return {"added": "insertions", "removed": "deletions", "moved": "moves"}.get(c.kind, "replacements")


def _anchors(ot, nt, ops):
    """For each new token index, the old source word an insertion there sits after (and
    vice versa) — from the last aligned (equal) pair before it."""
    o_after = [None] * (len(nt) + 1)   # new index -> old token index of last equal pair before it
    n_after = [None] * (len(ot) + 1)
    last_o = last_n = None
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for k in range(i2 - i1):
                o_after[j1 + k] = last_o; n_after[i1 + k] = last_n
                last_o, last_n = i1 + k, j1 + k
        else:
            for j in range(j1, j2):
                o_after[j] = last_o
            for i in range(i1, i2):
                n_after[i] = last_n
    return o_after, n_after


def _anchor(tokens, idx):
    if idx is not None:
        return (tokens[idx].src[-1], "after")
    return (tokens[0].src[0], "before") if tokens else None


def _finalise(old_words, new_words, ot, nt, raw: List[Change], ops=None) -> ContentDiff:
    from app.services.structural_alignment_service import classify_change

    raw = [c for c in raw if c.old_tok or c.new_tok]
    # document order: position in the new document (old-only changes are placed where
    # the alignment puts their neighbourhood in the new document)
    o2n = _old_to_new_positions(ot, nt)
    if ops is not None:
        o_after, n_after = _anchors(ot, nt, ops)
    raw.sort(key=lambda c: (min(c.new_tok) if c.new_tok else o2n[min(c.old_tok)],
                            min(c.old_tok) if c.old_tok else -1))
    for n, c in enumerate(raw):
        c.id = f"r{n}"
        c.old_words = sorted({s for i in c.old_tok for s in ot[i].src})
        c.new_words = sorted({s for j in c.new_tok for s in nt[j].src})
        c.old_text = " ".join(_display(ot[i]) for i in c.old_tok)
        c.new_text = " ".join(_display(nt[j]) for j in c.new_tok)
        c.filter_group = _filter_group(c)
        if ops is not None:
            if not c.old_tok and c.new_tok:
                c.old_anchor = _anchor(ot, o_after[min(c.new_tok)])
            if not c.new_tok and c.old_tok:
                c.new_anchor = _anchor(nt, n_after[min(c.old_tok)])
        if c.category == "content":
            c.change_type, meta = classify_change(c.old_text, c.new_text, c.kind)
            c.metadata = {**meta, **c.metadata}
        else:
            c.change_type = c.category if c.category != "typographic" else (c.subtype or "typographic")
            c.metadata = {"category": c.category, "subtype": c.subtype, **c.metadata}
    return ContentDiff(old_words, new_words, ot, nt, raw)


def _old_to_new_positions(ot, nt) -> List[float]:
    ops = SequenceMatcher(None, [t.key for t in ot], [t.key for t in nt], autojunk=False).get_opcodes()
    pos = [0.0] * len(ot)
    for tag, i1, i2, j1, j2 in ops:
        for k, i in enumerate(range(i1, i2)):
            pos[i] = (j1 + k) if tag == "equal" else (j1 - 0.5)
    return pos


# ---------------------------------------------------------------- views
_MARK_TYPE = {"removed": ("removed", None), "added": (None, "added"), "modified": ("changed", "changed"),
              "moved": ("removed", "added")}


def to_overlay(diff: ContentDiff):
    """(old_marks, new_marks, changes) in the shape render_orchestrator expects.
    Only visible categories are marked; hidden ones are listed separately by callers
    via ``suppressed_changes``."""
    old_marks, new_marks, changes = [], [], []
    for c in diff.changes:
        ot_type, nt_type = _MARK_TYPE[c.kind]
        if c.kind == "moved":
            ot_type = "removed" if c.old_words else None
            nt_type = "added" if c.new_words else None
        for i in c.old_words:
            old_marks.append({"index": i, "type": ot_type or "removed", "change_id": c.id})
        for j in c.new_words:
            new_marks.append({"index": j, "type": nt_type or "added", "change_id": c.id})
        changes.append(_change_dict(c))
    return old_marks, new_marks, changes


def suppressed_changes(diff: ContentDiff) -> List[dict]:
    return [_change_dict(c) for c in diff.changes if not c.default_visible]


def _change_dict(c: Change) -> dict:
    d = {"id": c.id, "kind": c.kind, "change_type": c.change_type, "metadata": c.metadata,
         "category": c.category, "subtype": c.subtype, "filter_group": c.filter_group,
         "default_visible": c.default_visible,
         "old_text": c.old_text, "new_text": c.new_text,
         "old_anchor": c.old_anchor, "new_anchor": c.new_anchor}
    if c.move_id:
        d["move_id"] = c.move_id
    return d


def to_blocks(diff: ContentDiff) -> List[dict]:
    """Text-redline DiffBlocks (equal / replace / insert / delete, moves flagged) built from
    the same visible change records as the overlay."""
    ot, nt = diff.old_tokens, diff.new_tokens
    o_change: Dict[int, Change] = {}
    n_change: Dict[int, Change] = {}
    for c in diff.changes:
        for i in c.old_tok:
            o_change[i] = c
        for j in c.new_tok:
            n_change[j] = c

    keys_o, keys_n = [t.key for t in ot], [t.key for t in nt]
    ops = SequenceMatcher(None, keys_o, keys_n, autojunk=False).get_opcodes()
    blocks: List[dict] = []
    cur_o: List[dict] = []
    cur_n: List[dict] = []

    def word(t: CompToken, c: Optional[Change]) -> dict:
        w = {"text": _display(t), "changed": bool(c is not None and c.default_visible)}
        if c is not None:
            w.update(change_id=c.id, category=c.category, subtype=c.subtype, filter_group=c.filter_group)
        return w

    def flush():
        nonlocal cur_o, cur_n
        if not cur_o and not cur_n:
            return
        co, cn = any(w["changed"] for w in cur_o), any(w["changed"] for w in cur_n)
        if not co and not cn:
            b = {"type": "equal", "old_text": " ".join(w["text"] for w in cur_o),
                 "new_text": " ".join(w["text"] for w in cur_n)}
            if any(w.get("change_id") for w in cur_o + cur_n):   # hidden records live here
                b.update(old_words=cur_o, new_words=cur_n)
            blocks.append(b)
        elif cur_o and not cur_n:
            blocks.append({"type": "delete", "old_text": " ".join(w["text"] for w in cur_o), "old_words": cur_o})
        elif cur_n and not cur_o:
            blocks.append({"type": "insert", "new_text": " ".join(w["text"] for w in cur_n), "new_words": cur_n})
        else:
            blocks.append({"type": "replace", "old_words": cur_o, "new_words": cur_n})
        cur_o, cur_n = [], []

    def moved_block(side: str, toks: List[CompToken], c: Change):
        flush()
        key = "old_text" if side == "old" else "new_text"
        blocks.append({"type": "delete" if side == "old" else "insert", key: " ".join(_display(t) for t in toks),
                       "moved": True, "move_id": c.move_id, "change_id": c.id})

    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for a, b in zip(range(i1, i2), range(j1, j2)):
                ca, cb = o_change.get(a), n_change.get(b)
                cur_o.append(word(ot[a], ca))
                cur_n.append(word(nt[b], cb))
                if _ends_sentence(ot[a].text) or _ends_sentence(nt[b].text):
                    flush()
            continue
        # moved runs become their own linked blocks; everything else accumulates
        i = i1
        while i < i2:
            c = o_change.get(i)
            if c is not None and c.kind == "moved":
                run = [ot[i]]
                i += 1
                while i < i2 and o_change.get(i) is c:
                    run.append(ot[i]); i += 1
                moved_block("old", run, c)
                continue
            cur_o.append(word(ot[i], c)); i += 1
            if tag == "delete" and not cur_n and _ends_sentence(ot[i - 1].text):
                flush()                         # a deleted sentence is its own row
        j = j1
        while j < j2:
            c = n_change.get(j)
            if c is not None and c.kind == "moved":
                run = [nt[j]]
                j += 1
                while j < j2 and n_change.get(j) is c:
                    run.append(nt[j]); j += 1
                moved_block("new", run, c)
                continue
            cur_n.append(word(nt[j], c)); j += 1
            if tag == "insert" and not cur_o and _ends_sentence(nt[j - 1].text):
                flush()                         # an inserted sentence is its own row
    flush()
    return blocks


def page_alignment(diff: ContentDiff, n_old: int, n_new: int) -> List[List[Optional[int]]]:
    """Sheet pairs [[old_page|None, new_page|None], ...] for a side-by-side export: each old
    page is paired with the new page holding most of its aligned (equal) text; pages with no
    counterpart get a blank filler on the other side. Monotone in both documents."""
    from collections import Counter

    ot, nt = diff.old_tokens, diff.new_tokens
    ops = SequenceMatcher(None, [t.key for t in ot], [t.key for t in nt], autojunk=False).get_opcodes()
    votes: Dict[int, Counter] = {}
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for k in range(i2 - i1):
                votes.setdefault(ot[i1 + k].page, Counter())[nt[j1 + k].page] += 1
    sheets: List[List[Optional[int]]] = []
    last_new = 0
    for o in range(1, n_old + 1):
        cand = [(n, v) for n, v in (votes.get(o) or Counter()).most_common() if n > last_new]
        n = cand[0][0] if cand else None
        if n is None:
            sheets.append([o, None])
            continue
        for skipped in range(last_new + 1, n):
            sheets.append([None, skipped])
        sheets.append([o, n])
        last_new = n
    for rest in range(last_new + 1, n_new + 1):
        sheets.append([None, rest])
    return sheets


# ---------------------------------------------------------------- matched text (hover units)
def _line_geometry(words: Sequence):
    """Right edge of every visual line, and the left/right extent of every reading block."""
    line_right: Dict[int, float] = {}
    block: Dict[int, List[float]] = {}
    for w in words:
        lid, bid = getattr(w, "line_id", 0), getattr(w, "block_id", 0)
        line_right[lid] = max(line_right.get(lid, w.x1), w.x1)
        ext = block.setdefault(bid, [w.x0, w.x1])
        ext[0], ext[1] = min(ext[0], w.x0), max(ext[1], w.x1)
    return line_right, block


def _para_break(prev, cur, geo) -> bool:
    """A new page, reading block, blank-line gap — or a line that stops well short of its
    column's right edge (the last line of a paragraph) — between two source words."""
    if prev.page != cur.page or getattr(prev, "block_id", 0) != getattr(cur, "block_id", 0):
        return True
    h = max(prev.y1 - prev.y0, cur.y1 - cur.y0, 1.0)
    if cur.y0 - prev.y1 > 1.2 * h:
        return True
    lid = getattr(prev, "line_id", 0)
    if lid == getattr(cur, "line_id", 0):
        return False
    line_right, block = geo
    left, right = block[getattr(prev, "block_id", 0)]
    return line_right[lid] < right - max(24.0, 0.15 * (right - left))


def match_segments(diff: ContentDiff) -> List[Tuple[List[int], List[int]]]:
    """Units of unchanged text linked across the documents, as (old source word indices,
    new source word indices): runs of aligned tokens between two change records, split at
    sentence ends, paragraph ends (blank-line gap or a short last line), reading-block and
    page breaks. Hovering one outlines
    its counterpart; clicking aligns both documents on it."""
    ot, nt = diff.old_tokens, diff.new_tokens
    o_changed = {i for c in diff.changes for i in c.old_tok}
    n_changed = {j for c in diff.changes for j in c.new_tok}
    ops = SequenceMatcher(None, [t.key for t in ot], [t.key for t in nt], autojunk=False).get_opcodes()
    ow, nw = diff.old_words, diff.new_words
    geo_o, geo_n = _line_geometry(ow), _line_geometry(nw)
    segs: List[Tuple[List[int], List[int]]] = []
    cur_o: List[int] = []
    cur_n: List[int] = []

    def flush():
        nonlocal cur_o, cur_n
        if cur_o and cur_n:
            segs.append((sorted(set(cur_o)), sorted(set(cur_n))))
        cur_o, cur_n = [], []

    for tag, i1, i2, j1, j2 in ops:
        if tag != "equal":
            flush()
            continue
        for a, b in zip(range(i1, i2), range(j1, j2)):
            if a in o_changed or b in n_changed:
                flush()
                continue
            if cur_o and (_para_break(ow[cur_o[-1]], ow[ot[a].src[0]], geo_o)
                          or _para_break(nw[cur_n[-1]], nw[nt[b].src[0]], geo_n)):
                flush()
            cur_o.extend(ot[a].src)
            cur_n.extend(nt[b].src)
            if _ends_sentence(ot[a].text) or _ends_sentence(nt[b].text):
                flush()
        flush()
    flush()
    return segs


# ---------------------------------------------------------------- running headers / footers
def compare_furniture(old_f: Sequence, new_f: Sequence, page_map: List[List[Optional[int]]]):
    """Running headers/footers/page numbers compared page against page along ``page_map``
    (they never take part in the body alignment). Returns (old_marks, new_marks, changes,
    match segments) with word indices into ``old_f`` / ``new_f`` and ids ``h0, h1, ...``."""
    old_marks: List[dict] = []
    new_marks: List[dict] = []
    changes: List[dict] = []
    segs: List[Tuple[List[int], List[int]]] = []
    n = 0
    for o_page, n_page in page_map:
        oi = [i for i, w in enumerate(old_f) if o_page is not None and w.page == o_page]
        ni = [j for j, w in enumerate(new_f) if n_page is not None and w.page == n_page]
        if not oi and not ni:
            continue
        d = compare_words([old_f[i] for i in oi], [new_f[j] for j in ni])
        remap = {}
        for c in d.changes:
            remap[c.id] = cid = f"h{n}"
            n += 1
            c.id = cid
            c.category, c.subtype, c.filter_group = "layout", "header-footer", "headers_footers"
            c.kind = "modified" if c.kind == "moved" else c.kind
            c.move_id = None
            c.change_type = "header_footer"
            c.metadata = {"category": "layout", "subtype": "header-footer"}
            c.old_words = [oi[k] for k in c.old_words]
            c.new_words = [ni[k] for k in c.new_words]
            if c.old_anchor and oi:
                c.old_anchor = (oi[c.old_anchor[0]], c.old_anchor[1])
            elif c.old_anchor:
                c.old_anchor = None
            if c.new_anchor and ni:
                c.new_anchor = (ni[c.new_anchor[0]], c.new_anchor[1])
            elif c.new_anchor:
                c.new_anchor = None
        om, nm, ch = to_overlay(d)
        old_marks += om
        new_marks += nm
        changes += ch
        segs += [([oi[k] for k in o], [ni[k] for k in m]) for o, m in match_segments(d)]
    return old_marks, new_marks, changes, segs
