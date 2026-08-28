"""Editing an uploaded DOCX's own body in place, rather than rebuilding it.

``lexical_export`` used to clone the upload, empty its ``w:body`` and write the
editor's HTML back as fresh paragraphs. That carried ``styles.xml``, ``sectPr``
and the headers across — but the body's *own* formatting lives on the body's
own XML, so emptying it threw away every font, size, colour, indent, spacing
and table style the document set directly. The export came back default-styled
Word even when the reviewer had changed nothing.

The formatting cannot be recovered from the HTML either: mammoth converts a
DOCX to *semantic* HTML (headings, lists, tables, bold/italic/underline) and
Lexical persists the same vocabulary, so "Georgia 22pt #003399, centred" was
already gone at the import boundary. It only ever survives in one place — the
original XML.

So the original XML is what gets edited. Each ``w:p`` and ``w:tbl`` keeps its
``pPr``/``rPr``/``tblPr``; only the *text inside the runs* is rewritten, run by
run, against the reviewer's version. That is what "replace the content, keep
the document" means, and it is what Word itself does when a person types.

How the two sides are lined up
------------------------------
Two alignments, both ``difflib``:

* **Blocks.** The body's text-bearing ``w:p``/``w:tbl`` children are aligned
  against the editor's blocks on normalized text. Matched pairs are patched,
  unmatched originals are removed, unmatched edits are inserted as a clone of
  their neighbour (so a new paragraph is set like the one it follows).
* **Characters, inside a matched paragraph.** The new text is diffed against
  the old and each surviving character is handed back to the run it came from;
  replaced and inserted text inherits the run it displaced. A paragraph of
  "8% a year" corrected to "6% a year" keeps its font, because the run that
  held "8" is the run that now holds "6".

Two deliberate asymmetries:

* **Empty originals never take part.** mammoth drops blank paragraphs, so the
  editor never held them; aligning them would read every one as a reviewer
  deletion and re-flow the page. They are left exactly where they are.
* **Editor formatting is applied, never removed.** A word bolded in the editor
  comes back bold; ``rPr`` the editor cannot express is left alone. Setting the
  formats the editor does not carry to *off* would strip the bold a paragraph
  style supplies — the very styling this module exists to preserve.
"""
from __future__ import annotations

import copy
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

logger = logging.getLogger(__name__)

_P = qn("w:p")
_TBL = qn("w:tbl")
_TR = qn("w:tr")
_TC = qn("w:tc")
_TCPR = qn("w:tcPr")
_VMERGE = qn("w:vMerge")
_R = qn("w:r")
_T = qn("w:t")
_BR = qn("w:br")
_TAB = qn("w:tab")
_CR = qn("w:cr")
_PPR = qn("w:pPr")
_RPR = qn("w:rPr")
_SECTPR = qn("w:sectPr")

# Runs also live one level down inside these. A hyperlink's runs are the
# sentence's runs; skipping them would read the linked words as deleted.
_RUN_WRAPPERS = (qn("w:hyperlink"), qn("w:ins"), qn("w:smartTag"), qn("w:sdt"),
                 qn("w:sdtContent"))

# Runs inside these are NOT this paragraph's text: a text box is its own
# region (``lexical_import`` imports it separately), and moveFrom/del are
# tracked-change residue that ``preprocessing_service`` already skips.
_SKIPPED_WRAPPERS = (qn("w:txbxContent"), qn("w:moveFrom"), qn("w:del"))

# Run children that carry text, and therefore get rewritten. Everything else a
# run holds — a drawing, a field, a footnote reference — is opaque and is
# carried over untouched.
_TEXTUAL = (_T, _BR, _TAB, _CR)

_FORMAT_ADDERS = {
    "bold": "get_or_add_b",
    "italic": "get_or_add_i",
    "underline": "get_or_add_u",
    "strike": "get_or_add_strike",
}

_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Whitespace-insensitive text, for lining blocks up.

    HTML collapses whitespace and Word does not, so the two sides disagree
    about spacing on text neither of them changed.
    """
    return _WS.sub(" ", text or "").strip()


# --- What the editor produced ------------------------------------------------
#
# Deliberately not BeautifulSoup: the HTML vocabulary is lexical_export's
# business, and this module's is OOXML. It is handed plain data.


@dataclass
class EditedParagraph:
    """One block of the reviewer's document, as (text, formats) pieces.

    ``formats`` is the same vocabulary ``lexical_export._INLINE_FORMATS`` maps
    onto — "bold", "italic", "underline", "strike".
    """

    pieces: List[Tuple[str, FrozenSet[str]]] = field(default_factory=list)
    # `src` of every <img> in the block. Opaque here — embedding one needs the
    # Document, not the element — but it decides whether an image-only
    # paragraph is a block at all, so it has to travel with the block.
    images: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(text for text, _ in self.pieces)


@dataclass
class EditedTable:
    """A table as rows of cells of paragraphs."""

    rows: List[List[List[EditedParagraph]]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(
            p.text for row in self.rows for cell in row for p in cell
        )


EditedBlock = object  # EditedParagraph | EditedTable


# --- Reading the original ----------------------------------------------------


def _runs(element) -> List:
    """Every text-bearing run under `element`, in document order."""
    out: List = []
    for child in element:
        tag = child.tag
        if tag in _SKIPPED_WRAPPERS:
            continue
        if tag == _R:
            out.append(child)
        elif tag in _RUN_WRAPPERS:
            out.extend(_runs(child))
    return out


def _run_text(run) -> str:
    """A run's text, with its breaks and tabs as the characters they are —
    so the character diff can match, keep or drop them like any other."""
    parts: List[str] = []
    for child in run:
        if child.tag == _T:
            parts.append(child.text or "")
        elif child.tag in (_BR, _CR):
            parts.append("\n")
        elif child.tag == _TAB:
            parts.append("\t")
    return "".join(parts)


def paragraph_text(paragraph) -> str:
    return "".join(_run_text(r) for r in _runs(paragraph))


def _table_text(table) -> str:
    return " ".join(
        paragraph_text(p)
        for tr in table.findall(_TR)
        for tc in tr.findall(_TC)
        for p in tc.findall(_P)
    )


def container_text(container) -> str:
    """Every word `container` holds, for the callers that need to know what the
    body already says before it is patched."""
    return " ".join(
        paragraph_text(c) if c.tag == _P else _table_text(c)
        for c in container
        if c.tag in (_P, _TBL)
    )


@dataclass
class _Original:
    element: object
    kind: str  # "p" | "tbl"
    text: str


def _originals(container) -> List[_Original]:
    """The blocks of `container` that take part in the alignment.

    Blocks with no text are excluded on purpose — see the module docstring.
    A blank paragraph is a line the layout depends on and an empty table is a
    layout grid (these documents are full of them: a 5x2 with nothing in it,
    holding a logo block square). Neither has any content to correct, and
    aligning them would read whichever side drops them as a deletion.
    """
    out: List[_Original] = []
    for child in container:
        if child.tag == _P:
            text = paragraph_text(child)
            if normalize(text):
                out.append(_Original(child, "p", text))
        elif child.tag == _TBL:
            text = _table_text(child)
            if normalize(text):
                out.append(_Original(child, "tbl", text))
    return out


# --- Writing runs ------------------------------------------------------------


_OFF = (None, "0", "false", "off")


def _apply_formats(run, formats: FrozenSet[str]) -> None:
    """Turn `formats` on. Never off — see the module docstring.

    A format already on is left exactly as the document wrote it: a bare
    ``<w:b/>`` and ``<w:b w:val="1"/>`` mean the same thing to Word, and
    rewriting one as the other would make every bold run in the file report as
    changed for no reason at all.
    """
    if not formats:
        return
    rPr = run.get_or_add_rPr()
    for fmt in sorted(formats):
        adder = _FORMAT_ADDERS.get(fmt)
        if adder is None:
            continue
        element = getattr(rPr, adder)()  # bare, which is already "on"
        value = element.get(qn("w:val"))
        if fmt == "underline":
            # Unlike b/i/strike, a bare <w:u/> is not an underline.
            if value in _OFF or value == "none":
                element.set(qn("w:val"), "single")
        elif value in _OFF and value is not None:
            del element.attrib[qn("w:val")]  # explicitly off, and now on


def _new_run(source, text: str, formats: FrozenSet[str], opaque: Sequence = ()):
    """A run formatted like `source` holding `text`.

    `source` is deep-copied so the original's ``rPr`` — the font, the size, the
    colour, everything the HTML never carried — comes across whole.
    """
    run = copy.deepcopy(source) if source is not None else OxmlElement("w:r")
    for child in list(run):
        if child.tag != _RPR:
            run.remove(child)
    _apply_formats(run, formats)
    for element in opaque:
        run.append(copy.deepcopy(element))
    for i, line in enumerate(text.split("\n")):
        if i:
            run.add_br()
        for j, part in enumerate(line.split("\t")):
            if j:
                run.add_tab()
            if part:
                run.add_t(part)
    return run


def _replace_runs(runs: List, outputs: Dict[int, List[Tuple[str, FrozenSet[str]]]]) -> None:
    """Swap each original run for the runs its text now needs.

    A run keeps its position, so anything else the paragraph holds — a
    bookmark, a comment marker, a proofing tag — stays where it was.
    """
    for i, run in enumerate(runs):
        opaque = [c for c in run if c.tag not in _TEXTUAL and c.tag != _RPR]
        specs = outputs.get(i, [])
        replacements = [
            _new_run(run, text, formats, opaque if j == 0 else ())
            for j, (text, formats) in enumerate(specs)
        ]
        if not replacements and opaque:
            # A drawing-only run: it holds no text to rewrite and must survive.
            replacements = [_new_run(run, "", frozenset(), opaque)]
        parent = run.getparent()
        position = parent.index(run)
        parent.remove(run)
        for offset, replacement in enumerate(replacements):
            parent.insert(position + offset, replacement)


def _owners(runs: List) -> List[int]:
    """Run index per character of the paragraph's text."""
    owners: List[int] = []
    for i, run in enumerate(runs):
        owners.extend([i] * len(_run_text(run)))
    return owners


def _assign(old: str, new: str, owners: List[int]) -> List[Optional[int]]:
    """The run each character of `new` belongs to.

    Surviving text goes back to its own run. Replaced text takes over the run
    it displaced; inserted text joins the run before it, which is where Word
    puts a character typed at a boundary.
    """
    assignment: List[Optional[int]] = [None] * len(new)
    if not owners:
        return assignment
    matcher = SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(j2 - j1):
                assignment[j1 + k] = owners[i1 + k]
        elif tag in ("replace", "insert"):
            if tag == "insert" and i1 > 0:
                owner = owners[i1 - 1]
            else:
                owner = owners[i1] if i1 < len(owners) else owners[-1]
            for k in range(j1, j2):
                assignment[k] = owner
    return assignment


def patch_paragraph(paragraph, edited: EditedParagraph) -> None:
    """Rewrite `paragraph`'s text to `edited`'s, keeping its formatting."""
    runs = _runs(paragraph)
    owners = _owners(runs)
    if not owners:
        # Nothing to diff against: no runs at all, or only runs that hold no
        # text (a picture on its own line). Its text is simply written in.
        _fill_paragraph(paragraph, edited, runs[0] if runs else None)
        return

    old = "".join(_run_text(r) for r in runs)
    characters = [(ch, formats) for text, formats in edited.pieces for ch in text]
    new = "".join(ch for ch, _ in characters)
    assignment = _assign(old, new, owners)

    outputs: Dict[int, List[Tuple[str, FrozenSet[str]]]] = {}
    previous: Optional[Tuple[Optional[int], FrozenSet[str]]] = None
    for (ch, formats), owner in zip(characters, assignment):
        if previous != (owner, formats):
            outputs.setdefault(owner, []).append(("", formats))
            previous = (owner, formats)
        text, fmt = outputs[owner][-1]
        outputs[owner][-1] = (text + ch, fmt)
    _replace_runs(runs, outputs)


def _fill_paragraph(paragraph, edited: EditedParagraph, source) -> None:
    """Write `edited` into `paragraph` from scratch, formatted like `source`."""
    for run in _runs(paragraph):
        run.getparent().remove(run)
    for text, formats in edited.pieces:
        if text:
            paragraph.append(_new_run(source, text, formats))


# --- Tables ------------------------------------------------------------------


def _patch_table(table, edited: EditedTable) -> None:
    """Rewrite a table's cells. The table's own formatting — its style, its
    borders, its shading, its column widths — is never touched."""
    rows = table.findall(_TR)
    paired = min(len(rows), len(edited.rows))
    for i in range(paired):
        _patch_row(rows[i], edited.rows[i])
    for surplus in rows[paired:]:
        table.remove(surplus)
    # A row the reviewer added is a clone of the last one, so it is banded and
    # bordered like the rows above it.
    previous = rows[paired - 1] if paired else None
    for extra in edited.rows[paired:]:
        if previous is None:
            break
        clone = copy.deepcopy(previous)
        previous.addnext(clone)
        _patch_row(clone, extra)
        previous = clone


def _patch_row(row, cells: List[List[EditedParagraph]]) -> None:
    """Cells are matched by position. A surplus cell on either side is left
    alone: adding or dropping a column is a structural change, not a
    correction, and guessing at one would break the table's grid."""
    for cell, edited in zip(_editable_cells(row), cells):
        patch_container(cell, list(edited))


def _editable_cells(row) -> List:
    """A row's cells, minus the ones a vertical merge continues into.

    Word writes a `w:tc` in every row a merge passes through and requires the
    continuations to be empty; HTML says the same thing with one `rowspan` and
    no cell at all. Counting the continuations would pair the row's first real
    value with an empty spanned cell and slide every value after it one column
    right — which is how a charge table ends up reading off by one.
    """
    out = []
    for cell in row.findall(_TC):
        merge = cell.find(f"{_TCPR}/{_VMERGE}")
        if merge is not None and (merge.get(qn("w:val")) or "continue") != "restart":
            continue
        out.append(cell)
    return out


# --- Blocks ------------------------------------------------------------------


def _key(kind: str, text: str) -> str:
    """Alignment key. Kind is part of it so a table can only ever match a
    table — a paragraph patched into a table's place would lose the table."""
    return f"{kind}:{normalize(text)}"


def _ambiguous(before: List[str], after: List[str]):
    """``isjunk`` for the block alignment: is this key too repeated to anchor on?

    ``SequenceMatcher`` finds the *longest common subsequence*, and an LCS is
    free to match any equal pair it likes. These documents repeat a line
    verbatim — "Terms and conditions apply." under three different tables, each
    set differently — and when the reviewer edits the second one, LCS happily
    matches the first original to the first edit and the SECOND original to the
    THIRD edit. The one real edit then reads as an insert in one place and a
    delete in another: a paragraph's formatting is dropped and everything below
    it shifts up a slot.

    So a key is an anchor only when it occurs exactly once on each side, which
    is the rule patience diff is built on. difflib excludes junk from its index
    but still absorbs it into a match that reaches it, so repeated boilerplate
    sitting next to unique content still aligns; what is left over lands in a
    ``replace``, which is paired off positionally — and position is exactly the
    evidence LCS was throwing away.
    """
    before_counts, after_counts = Counter(before), Counter(after)

    def is_ambiguous(key: str) -> bool:
        return before_counts[key] != 1 or after_counts[key] != 1

    return is_ambiguous


def _first_empty_paragraph(container):
    """An empty ``w:p`` sitting where an insert would go, if there is one.

    An empty table cell is one empty paragraph, and filling it is what the
    reviewer means; adding a second would double-space the cell.
    """
    for child in container:
        if child.tag == _TBL:
            return None
        if child.tag == _P:
            return child if not normalize(paragraph_text(child)) else None
    return None


def _insert_after(container, element, after) -> None:
    if after is not None:
        after.addnext(element)
        return
    anchor = next((c for c in container if c.tag in (_P, _TBL, _SECTPR)), None)
    if anchor is not None:
        anchor.addprevious(element)
    else:
        container.append(element)


def _blank_paragraph(model):
    """A paragraph set like `model` with nothing in it yet."""
    if model is None:
        return OxmlElement("w:p")
    paragraph = copy.deepcopy(model)
    for child in list(paragraph):
        if child.tag != _PPR:
            paragraph.remove(child)
    return paragraph


def patch_container(container, edited: List[EditedBlock]) -> List[Tuple[EditedBlock, object]]:
    """Rewrite `container`'s blocks to `edited`, keeping their formatting.

    `container` is anything that holds block-level content: a ``w:body``, a
    table cell, a header, a footer, a text box.

    Returns each block paired with the element it now lives in, so a caller
    that has something left to place — a picture, which needs the Document
    rather than the element — knows where it goes.
    """
    originals = _originals(container)
    edited = [
        block
        for block in edited
        # An image-only paragraph has no text and is still a block: it is the
        # picture's position. Every other empty block is dropped, so a blank
        # line neither inserts one nor deletes the ones already there.
        if normalize(getattr(block, "text", "")) or getattr(block, "images", None)
    ]

    before = [_key(o.kind, o.text) for o in originals]
    after = [_key("tbl" if isinstance(b, EditedTable) else "p", b.text) for b in edited]
    matcher = SequenceMatcher(_ambiguous(before, after), before, after, autojunk=False)

    # Ops are emitted in document order, so "the element before this one" is
    # simply the last one touched — which is where an insert belongs.
    placements: List[Tuple[EditedBlock, object]] = []
    previous = None
    model = next((o.element for o in originals if o.kind == "p"), None)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        paired = min(i2 - i1, j2 - j1) if tag == "replace" else (i2 - i1 if tag == "equal" else 0)
        for k in range(paired):
            original, block = originals[i1 + k], edited[j1 + k]
            _patch_block(original, block)
            placements.append((block, original.element))
            previous = original.element
            if original.kind == "p":
                model = original.element
        if tag in ("replace", "delete"):
            for original in originals[i1 + paired:i2]:
                original.element.getparent().remove(original.element)
        if tag in ("replace", "insert"):
            for block in edited[j1 + paired:j2]:
                previous = _insert_block(container, block, previous, model)
                placements.append((block, previous))
    return placements


def _patch_block(original: _Original, block: EditedBlock) -> None:
    if original.kind == "tbl" and isinstance(block, EditedTable):
        _patch_table(original.element, block)
    elif original.kind == "p" and isinstance(block, EditedParagraph):
        patch_paragraph(original.element, block)


def _insert_block(container, block: EditedBlock, previous, model):
    """Add a block the reviewer wrote, set like the one it follows."""
    if isinstance(block, EditedTable):
        # A table with no counterpart has no formatting to inherit, and
        # inventing one would be a table the document never had. Its wording is
        # still the reviewer's, so it comes back as paragraphs.
        for cell_paragraphs in (cell for row in block.rows for cell in row):
            for paragraph in cell_paragraphs:
                previous = _insert_block(container, paragraph, previous, model)
        return previous

    element = _first_empty_paragraph(container) if previous is None else None
    if element is None:
        element = _blank_paragraph(model)
        _insert_after(container, element, previous)
    _fill_paragraph(element, block, next(iter(_runs(model)), None) if model is not None else None)
    return element
