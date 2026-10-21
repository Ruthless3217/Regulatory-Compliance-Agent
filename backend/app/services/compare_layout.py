"""Reading-order repair for positioned words (Compare tool).

Words must be compared in *content* order, not by y-sorting across the full page width:
that interleaves the two columns of a two-column page and the wrapped lines of
neighbouring table cells, so identical text looks changed.

Order produced per page:
  * ruled-table regions (cell boxes from pdfplumber) are atomic and read cell by cell,
    row-major, each cell's lines top-down;
  * other text is split into column zones by a gutter-first recursive cut — a column
    gutter is searched among *narrow* line segments only, and full-width segments that
    cross it (page headers, full-width headings) cut the region into horizontal bands;
  * within a block, words are grouped into visual lines by vertical overlap (robust to
    sub-point baseline jitter) and read left to right.

Every word gets ``block_id`` (table cell / column block — unique per document) and
``line_id`` (visual line, increasing in reading order). Pure geometry — works for
text-layer and OCR words alike. Words are not copied: the same objects are returned
in a new order with ids filled in.
"""
from statistics import median
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

BBox = Tuple[float, float, float, float]  # x0, top, x1, bottom

_MIN_GUTTER = 6.0          # pt — never treat a gap narrower than this as a column gutter
_GUTTER_GAP_FACTOR = 2.0   # × median inter-word gap on the page (justified text gaps vary;
                           # the multi-row / both-sides checks below reject false gutters)

_MIN_SIDE_ITEMS = 3        # each column needs at least this many segments
_MIN_SIDE_SHARE = 0.15     # and must cover at least this share of the region width
_MIN_Y_OVERLAP = 0.5       # columns must sit side by side (y-ranges overlap)


# ---------------------------------------------------------------- helpers
def _h(w) -> float:
    return max(0.1, w.y1 - w.y0)


def _same_line(a_top: float, a_bot: float, b_top: float, b_bot: float) -> bool:
    overlap = min(a_bot, b_bot) - max(a_top, b_top)
    return overlap >= _MIN_Y_OVERLAP * min(a_bot - a_top, b_bot - b_top)


def _lines(words: Sequence) -> List[List]:
    """Group words into visual lines (vertical overlap), lines top-down, words left-right."""
    lines: List[Tuple[float, float, List]] = []
    for w in sorted(words, key=lambda w: (w.y0 + w.y1) / 2):
        for i, (top, bot, members) in enumerate(lines):
            if _same_line(top, bot, w.y0, w.y1):
                members.append(w)
                lines[i] = (min(top, w.y0), max(bot, w.y1), members)
                break
        else:
            lines.append((w.y0, w.y1, [w]))
    lines.sort(key=lambda t: (t[0] + t[1]) / 2)
    return [sorted(m, key=lambda w: w.x0) for _, _, m in lines]


class _Item:
    """A layout unit for the cut: a line segment (run of words) or a whole table."""

    __slots__ = ("x0", "y0", "x1", "y1", "words", "cells")

    def __init__(self, words: List, cells: Optional[List[Tuple[BBox, List]]] = None, box: Optional[BBox] = None):
        self.words = words
        self.cells = cells
        if box is None:
            box = (min(w.x0 for w in words), min(w.y0 for w in words), max(w.x1 for w in words), max(w.y1 for w in words))
        self.x0, self.y0, self.x1, self.y1 = box


def _segments(words: Sequence, gap: float) -> List[_Item]:
    out: List[_Item] = []
    for line in _lines(words):
        cur = [line[0]]
        for w in line[1:]:
            if w.x0 - cur[-1].x1 > gap:
                out.append(_Item(cur)); cur = [w]
            else:
                cur.append(w)
        out.append(_Item(cur))
    return out


def _group_tables(cells: Iterable[BBox]) -> List[List[BBox]]:
    """Connected components of touching/overlapping cell boxes = one table each."""
    cells = [c for c in cells if c[2] > c[0] and c[3] > c[1]]
    parent = list(range(len(cells)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i

    for i, a in enumerate(cells):
        for j in range(i + 1, len(cells)):
            b = cells[j]
            if a[0] <= b[2] + 1 and b[0] <= a[2] + 1 and a[1] <= b[3] + 1 and b[1] <= a[3] + 1:
                parent[find(i)] = find(j)
    groups: Dict[int, List[BBox]] = {}
    for i, c in enumerate(cells):
        groups.setdefault(find(i), []).append(c)
    return list(groups.values())


def _rows_major(cells: List[BBox]) -> List[BBox]:
    cells = sorted(set(cells), key=lambda c: (c[1], c[0]))
    rows: List[List[BBox]] = []
    for c in cells:
        if rows and abs(rows[-1][0][1] - c[1]) <= 2.0:
            rows[-1].append(c)
        else:
            rows.append([c])
    return [c for row in rows for c in sorted(row, key=lambda c: c[0])]


def _assign_to_tables(words: Sequence, cells: Sequence[BBox]) -> Tuple[List[_Item], List]:
    """Pull words inside ruled tables into atomic table items; return (tables, rest)."""
    tables: List[_Item] = []
    rest = list(words)
    for group in _group_tables(cells):
        ordered = _rows_major(group)
        tb = (min(c[0] for c in group), min(c[1] for c in group), max(c[2] for c in group), max(c[3] for c in group))
        buckets: List[Tuple[BBox, List]] = [(c, []) for c in ordered]
        stray: List = []
        keep: List = []
        for w in rest:
            cx, cy = (w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2
            if not (tb[0] <= cx <= tb[2] and tb[1] <= cy <= tb[3]):
                keep.append(w); continue
            best = None
            for c, members in buckets:
                if c[0] <= cx <= c[2] and c[1] <= cy <= c[3]:
                    area = (c[2] - c[0]) * (c[3] - c[1])
                    if best is None or area < best[0]:
                        best = (area, members)
            (best[1] if best else stray).append(w)
        rest = keep
        if any(m for _, m in buckets) or stray:
            if stray:
                buckets.append((tb, stray))
            tables.append(_Item([w for _, m in buckets for w in m], cells=[(c, m) for c, m in buckets if m], box=tb))
    return tables, rest


# ---------------------------------------------------------------- the cut
def _rows(items: List[_Item]) -> List[List[_Item]]:
    rows: List[Tuple[float, float, List[_Item]]] = []
    for it in sorted(items, key=lambda i: (i.y0 + i.y1) / 2):
        for k, (top, bot, members) in enumerate(rows):
            if _same_line(top, bot, it.y0, it.y1):
                members.append(it); rows[k] = (min(top, it.y0), max(bot, it.y1), members)
                break
        else:
            rows.append((it.y0, it.y1, [it]))
    return [m for _, _, m in rows]


def _find_gutter(items: List[_Item], gutter: float) -> Optional[Tuple[float, float]]:
    """A vertical whitespace strip (>= ``gutter`` wide) that most rows are *split* by.

    Votes per row: a row is 'split' at x if it has text on both sides and none over x,
    'crossing' if some item covers x. A gutter needs >= _MIN_SIDE_ITEMS split rows, at
    most half as many crossing rows (full-width headings may cross it — they become band
    separators), and substantial text on both sides. Full-width paragraphs cross every
    candidate, so ordinary single-column text never yields a gutter.
    """
    rx0, rx1 = min(i.x0 for i in items), max(i.x1 for i in items)
    width = max(1.0, rx1 - rx0)
    rows = _rows(items)
    lo, hi = int(rx0) + 1, int(rx1)
    if hi - lo < gutter:
        return None
    split = [0] * (hi - lo)
    cross = [0] * (hi - lo)
    for row in rows:
        for k in range(hi - lo):
            x = lo + k + 0.5
            if any(i.x0 <= x <= i.x1 for i in row):
                cross[k] += 1
            elif any(i.x1 < x for i in row) and any(i.x0 > x for i in row):
                split[k] += 1
    ok = [split[k] >= _MIN_SIDE_ITEMS and cross[k] * 2 <= split[k] for k in range(hi - lo)]
    best = None
    k = 0
    while k < len(ok):
        if not ok[k]:
            k += 1; continue
        j = k
        while j < len(ok) and ok[j]:
            j += 1
        # narrow the run to its cleanest strip (fewest crossing rows). Take the leftmost
        # clean strip that is wide enough: a clause number hanging between the columns
        # ("11." just left of the right column's text) belongs to the right column.
        mc = min(cross[k:j])
        strips, a = [], k
        while a < j:
            if cross[a] != mc:
                a += 1; continue
            b = a
            while b < j and cross[b] == mc:
                b += 1
            strips.append((a, b)); a = b
        wide = [s for s in strips if (s[1] - s[0]) >= gutter]
        sub = wide[0] if wide else max(strips, key=lambda s: s[1] - s[0])
        # snap the 1pt grid strip back to the exact whitespace between text edges
        c0, c1 = lo + sub[0] + 0.5, lo + sub[1] - 0.5
        g = (max((i.x1 for i in items if i.x1 < c0), default=c0),
             min((i.x0 for i in items if i.x0 > c1), default=c1))
        votes = max(split[sub[0]:sub[1]])
        if (g[1] - g[0]) >= gutter:
            left = [i for i in items if i.x1 <= g[0]]
            right = [i for i in items if i.x0 >= g[1]]
            if (left and right
                    and (max(i.x1 for i in left) - rx0) >= _MIN_SIDE_SHARE * width
                    and (rx1 - min(i.x0 for i in right)) >= _MIN_SIDE_SHARE * width):
                if best is None or votes > best[0] or (votes == best[0] and g[1] - g[0] > best[1][1] - best[1][0]):
                    best = (votes, g)
        k = j
    return best[1] if best else None


def _cut(items: List[_Item], gutter: float) -> List[List[_Item]]:
    """Return leaf blocks (lists of items) in reading order."""
    if len(items) <= 1:
        return [items] if items else []
    g = _find_gutter(items, gutter)
    if g is None:
        return [items]
    crossing = [i for i in items if i.x0 < g[1] and i.x1 > g[0]]
    if not crossing:
        left = [i for i in items if i.x1 <= g[0]]
        right = [i for i in items if i.x0 >= g[1]]
        return _cut(left, gutter) + _cut(right, gutter)
    # full-width items cut the region into bands; each band is cut on its own
    out: List[List[_Item]] = []
    band: List[_Item] = []
    for it in sorted(items, key=lambda i: (i.y0, i.x0)):
        if it in crossing:
            if band:
                out += _cut(band, gutter); band = []
            out.append([it])
        else:
            band.append(it)
    if band:
        out += _cut(band, gutter)
    return out


def _leaf_lines(block: List[_Item]):
    """Yield (block_key, line_words) for a leaf: tables expand to cells."""
    plain: List = []
    for it in sorted(block, key=lambda i: (i.y0, i.x0)):
        if it.cells is None:
            plain.append(it)
            continue
        if plain:
            yield ("text", [w for p in plain for w in p.words]); plain = []
        for _, members in it.cells:
            yield ("cell", members)
    if plain:
        yield ("text", [w for p in plain for w in p.words])


def order_words(
    words: Sequence,
    page_sizes: Optional[Dict[int, Tuple[float, float]]] = None,
    table_cells: Optional[Dict[int, Sequence[BBox]]] = None,
) -> List:
    """Return ``words`` in content reading order with ``block_id``/``line_id`` set."""
    table_cells = table_cells or {}
    by_page: Dict[int, List] = {}
    for w in words:
        by_page.setdefault(w.page, []).append(w)

    out: List = []
    block_id = 0
    line_id = 0
    for page in sorted(by_page):
        pw = by_page[page]
        gaps = []
        for line in _lines(pw):
            gaps += [b.x0 - a.x1 for a, b in zip(line, line[1:]) if b.x0 > a.x1]
        gutter = max(_MIN_GUTTER, _GUTTER_GAP_FACTOR * (median(gaps) if gaps else 3.0))

        tables, rest = _assign_to_tables(pw, table_cells.get(page, ()))
        items = tables + _segments(rest, gutter)
        for leaf in _cut(items, gutter):
            # a leaf of segments is one block; tables contribute one block per cell
            for kind, members in _leaf_lines(leaf):
                if not members:
                    continue
                for col, line in enumerate(_lines(members)):
                    for w in line:
                        w.block_id = block_id
                        w.line_id = line_id
                        out.append(w)
                    line_id += 1
                block_id += 1
    return out
