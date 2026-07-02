"""Zero-token structural brochure parser (Brochure Phase 1).

Turns a product-brochure PDF into typed, self-contained blocks using layout
statistics only — no LLM calls. pdfplumber (MIT, already a dependency) gives
per-word font size + position; everything else is arithmetic:

  * body font = statistical mode of character sizes; larger = heading
  * lines grouped by y-position; running headers/footers (same short text on
    ≥50% of pages) suppressed instead of becoming bogus sections
  * sections assembled under a heading stack; every chunk gets its heading
    path PREPENDED so retrieval hits are self-contained ("context inheritance")
  * tables are never text-chunked: extracted as structured rows + a
    deterministic natural-language summary (for embedding); words inside
    table bboxes are excluded from prose so numbers exist in exactly one place
  * UINs, the IRDAI-mandated product descriptor line, and the product name
    (from the running header) are extracted as metadata

Validated against the real eTouch II brochure — see test_brochure_parser.py
for the empirical golden assertions.
"""
from __future__ import annotations

import logging
import os
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Hard cap per section chunk (≈ tokens). Large sections are split.
MAX_SECTION_TOKENS = 480

#: A line must be this much larger than body text to count as a heading.
_HEADING_SIZE_DELTA = 1.5
#: Headings are short; longer big-font runs are usually styled body callouts.
_MAX_HEADING_CHARS = 120
#: Short text repeating on at least this fraction of pages = running header.
_RUNNING_HEADER_PAGE_FRACTION = 0.5
#: y-grouping tolerance for words on the same visual line (points).
_LINE_Y_TOLERANCE = 3.0
#: A "table" bbox covering more than this fraction of its page is a
#: false positive (a full-page border/ruling) — extracting it as a table
#: would swallow every word on the page into a meaningless grid and strip
#: the page of all prose. Ignore it so the text becomes normal sections.
_FULL_PAGE_TABLE_FRACTION = 0.85

_UIN_RE = re.compile(r"UIN[\s:\-]*([0-9]{2,3}[A-Z][0-9]{3}V[0-9]{2})")
#: The product name in the running-header-less marketing leaflets appears as a
#: ``Bajaj [] Life <Name>`` phrase in the body. Capture the lead word
#: plus up to a few following tokens; _trim_product_name keeps only the name.
_PRODUCT_PHRASE_RE = re.compile(
    r"Bajaj(?:\s+)?\s+Life\s+"
    r"([A-Z][A-Za-z0-9][A-Za-z0-9\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9\-]*){0,6})"
)
#: The insurer's own legal name — never a product. Used to reject both a
#: company-name running header and company-name body matches.
_COMPANY_NAME_RE = re.compile(r"bajaj\s+(?:\s+)?life\s+insurance", re.I)
_DESCRIPTOR_RE = re.compile(
    r"\bAn?\b.{0,80}?(Linked|Participating).{0,80}?Plan\b", re.IGNORECASE
)
_DISCLOSURE_CUES = (
    "uin", "risk factor", "section 41", "free look", "free-look",
    "gst", "goods and service tax", "prohibition of rebate", "grievance",
)


@dataclass
class BrochureTable:
    page_number: int
    table_index: int
    heading: Optional[str]
    rows: List[List[Optional[str]]]
    summary: str


@dataclass
class BrochureSection:
    heading_path: List[str]
    text: str
    chunk_text: str
    page_start: int
    block_type: str  # prose | disclosure
    token_count: int


@dataclass
class ParsedBrochure:
    source_file: str
    page_count: int
    body_font_size: float
    product_name: str
    descriptor: Optional[str]
    uin: Optional[str]
    uins: List[str]
    sections: List[BrochureSection] = field(default_factory=list)
    tables: List[BrochureTable] = field(default_factory=list)
    full_text: str = ""


def _count_tokens(text: str) -> int:
    try:
        import tiktoken
        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return max(1, len(text) // 4)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _group_lines(words: List[Dict]) -> List[Dict]:
    """Group pdfplumber words into visual lines by y-position."""
    lines: List[Dict] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1]["top"] - w["top"]) <= _LINE_Y_TOLERANCE:
            lines[-1]["words"].append(w)
        else:
            lines.append({"top": w["top"], "words": [w]})
    for ln in lines:
        ws = sorted(ln["words"], key=lambda w: w["x0"])
        ln["text"] = " ".join(w["text"] for w in ws)
        # size: max word size weighted toward the dominant run
        ln["size"] = round(max(w.get("size", 0.0) for w in ws), 1)
    return lines


def _bbox_area(bbox: Tuple[float, float, float, float]) -> float:
    x0, top, x1, bottom = bbox
    return abs((x1 - x0) * (bottom - top))


def _inside_any_bbox(word: Dict, bboxes: List[Tuple[float, float, float, float]]) -> bool:
    cx = (word["x0"] + word["x1"]) / 2.0
    cy = (word["top"] + word["bottom"]) / 2.0
    return any(x0 <= cx <= x1 and top <= cy <= bottom for (x0, top, x1, bottom) in bboxes)


def _table_summary(page_number: int, heading: Optional[str], rows: List[List]) -> str:
    """Deterministic NL summary (the embeddable face of a table). Zero LLM."""
    header = [str(c) for c in (rows[0] if rows else []) if c]
    where = f" under '{heading}'" if heading else ""
    cols = f" with columns: {', '.join(header[:8])}" if header else ""
    return (
        f"Table on page {page_number}{where}{cols}. "
        f"{max(0, len(rows) - 1)} data rows."
    )


def _block_type(text: str) -> str:
    low = text.lower()
    return "disclosure" if any(cue in low for cue in _DISCLOSURE_CUES) else "prose"


def _split_to_cap(body: str) -> List[str]:
    """Split an oversized section body on paragraph/line boundaries."""
    parts: List[str] = []
    buf: List[str] = []
    buf_tokens = 0
    for para in body.split("\n"):
        t = _count_tokens(para)
        if buf and buf_tokens + t > MAX_SECTION_TOKENS - 40:  # headroom for heading
            parts.append("\n".join(buf))
            buf, buf_tokens = [], 0
        buf.append(para)
        buf_tokens += t
    if buf:
        parts.append("\n".join(buf))
    return parts or [body]


#: ALL-CAPS keywords that sit next to a product name in the body but are never
#: part of it — they would otherwise pass the capital-letter name-word test.
_NAME_STOP_TOKENS = {"UIN"}


def _is_name_word(word: str) -> bool:
    """A token belongs to a product name if it leads with a capital or digit
    (Title-Case, ALL-CAPS, a roman numeral like 'II', or 'ROP'); lowercase
    connectors ('perfect', 'for', 'which', 'to') — and IRDAI keywords like
    'UIN' — mark the end of the name."""
    return (
        bool(word)
        and (word[0].isupper() or word[0].isdigit())
        and word.strip(".,:").upper() not in _NAME_STOP_TOKENS
    )


def _trim_product_name(raw: str) -> str:
    """Keep the leading run of name words, dropping the marketing tail
    ('Guaranteed Wealth Goal perfect for YOU' -> 'Guaranteed Wealth Goal')."""
    kept: List[str] = []
    for w in raw.split():
        if _is_name_word(w):
            kept.append(w)
        else:
            break
    return " ".join(kept).replace("- ", " ").strip(" -")


def _stem_to_name(source_path: str) -> str:
    """Last-resort product name from the filename when the document carries no
    usable running header or body phrase (e.g. iSecure)."""
    stem = os.path.splitext(os.path.basename(source_path))[0]
    cleaned = re.sub(r"[-_]+", " ", stem).strip()
    cleaned = re.sub(r"\s+(sl|brochure|leaflet|plan)$", "", cleaned, flags=re.I)
    return cleaned.title().strip()


def _extract_product_name(
    running: set, page_lines: List[Tuple[int, Dict]], source_path: str
) -> str:
    """Product name, most-reliable source first:

    1. the most-widespread running header (Phase-1 behaviour) — unless it is
       the insurer's legal name (marketing leaflets repeat 'Bajaj Life ...');
    2. the most-frequent ``Bajaj [] Life <Name>`` body phrase, excluding
       company boilerplate and riders — this is how header-less leaflets name
       their product;
    3. the filename, title-cased.
    """
    # 1. running header (largest font wins ties), skipping company boilerplate
    if running:
        best = max(
            running,
            key=lambda k: (len(set(p for p, l in page_lines if _norm(l["text"]) == k)),
                           max((l["size"] for p, l in page_lines if _norm(l["text"]) == k), default=0)),
        )
        for _, line in page_lines:
            if _norm(line["text"]) == best:
                disp = line["text"].strip()
                if disp and not _COMPANY_NAME_RE.search(disp):
                    return disp
                break

    # 2. product phrase in the body. Frequency alone can't separate the
    #    leaflet's subject from a companion product it cross-references
    #    (e.g. a 'Smart Secure ROP' leaflet that also discusses 'Secure Plus'),
    #    so the filename — which names the actual product — breaks the tie.
    norm_text = re.sub(r"\s+", " ", "\n".join(l["text"] for _, l in page_lines))
    counts: Counter = Counter()
    for m in _PRODUCT_PHRASE_RE.finditer(norm_text):
        name = _trim_product_name(m.group(1))
        low = name.lower()
        # 'Rider' often falls just past the captured window
        # ('... Accidental Permanent Total/Partial Disability Benefit Rider'),
        # so scan a short tail too before accepting the phrase as a product.
        tail = norm_text[m.end():m.end() + 50].lower()
        if not name or low.startswith("insurance") or "rider" in low or "rider" in tail:
            continue
        counts[f"Bajaj Life {name}"] += 1
    if counts:
        stem_tokens = set(re.findall(r"[a-z0-9]+", _stem_to_name(source_path).lower()))

        def _score(item: Tuple[str, int]) -> Tuple[int, int]:
            name, freq = item
            toks = set(re.findall(r"[a-z0-9]+", name.lower())) - {"bajaj", "life"}
            # filename overlap is authoritative (it names the subject product);
            # frequency only breaks ties when the filename is an abbreviation
            # (e.g. 'SWT') that overlaps nothing.
            return (len(toks & stem_tokens), freq)

        return max(counts.items(), key=_score)[0]

    # 3. filename fallback
    return _stem_to_name(source_path)


def parse_brochure(path: str) -> ParsedBrochure:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        n_pages = len(pdf.pages)

        # ---- pass 1: tables (and their bboxes, to exclude from prose) ------
        tables: List[BrochureTable] = []
        table_bboxes: Dict[int, List[Tuple[float, float, float, float]]] = {}
        page_lines: List[Tuple[int, Dict]] = []  # (page_number, line)

        size_counter: Counter = Counter()

        for pi, page in enumerate(pdf.pages, start=1):
            page_area = float(page.width) * float(page.height)
            found = [
                t for t in page.find_tables()
                if not page_area or _bbox_area(t.bbox) <= _FULL_PAGE_TABLE_FRACTION * page_area
            ]
            table_bboxes[pi] = [t.bbox for t in found]
            for ti, t in enumerate(found):
                rows = t.extract()
                # drop fully-empty rows
                rows = [r for r in rows if any(c not in (None, "") for c in r)]
                if rows:
                    tables.append(BrochureTable(
                        page_number=pi, table_index=ti, heading=None,
                        rows=rows, summary="",
                    ))

            words = page.extract_words(extra_attrs=["size"])
            for w in words:
                size_counter[round(w.get("size", 0.0), 1)] += len(w["text"])
            prose_words = [w for w in words if not _inside_any_bbox(w, table_bboxes[pi])]
            for line in _group_lines(prose_words):
                page_lines.append((pi, line))

        if not size_counter:
            logger.warning(f"brochure_parser: no extractable text in {path}")
            return ParsedBrochure(
                source_file=path, page_count=n_pages, body_font_size=0.0,
                product_name="", descriptor=None, uin=None, uins=[],
            )

        body_size = size_counter.most_common(1)[0][0]

        # ---- pass 2: running header/footer suppression ----------------------
        short_line_pages: Dict[str, set] = {}
        for pi, line in page_lines:
            key = _norm(line["text"])
            if key and len(key) <= 60:
                short_line_pages.setdefault(key, set()).add(pi)
        running = {
            key for key, pages in short_line_pages.items()
            if len(pages) >= max(2, int(n_pages * _RUNNING_HEADER_PAGE_FRACTION))
        }
        product_name = _extract_product_name(running, page_lines, path)

        # ---- pass 3: metadata -----------------------------------------------
        full_text = "\n".join(line["text"] for _, line in page_lines)
        uins = list(dict.fromkeys(_UIN_RE.findall(full_text)))
        page1_text = "\n".join(line["text"] for pi, line in page_lines if pi == 1)
        m = _DESCRIPTOR_RE.search(page1_text)
        descriptor = m.group(0).strip() if m else None

        # ---- pass 4: heading hierarchy + section assembly -------------------
        heading_sizes = sorted(
            {ln["size"] for _, ln in page_lines
             if ln["size"] >= body_size + _HEADING_SIZE_DELTA},
            reverse=True,
        )
        size_to_level = {s: i + 1 for i, s in enumerate(heading_sizes)}

        sections: List[BrochureSection] = []
        heading_stack: List[Tuple[int, str]] = []  # (level, text)
        body_buf: List[str] = []
        body_page_start = 1

        def flush() -> None:
            body = "\n".join(b for b in body_buf if b.strip()).strip()
            if not body or not heading_stack:
                body_buf.clear()
                return
            path_titles = [t for _, t in heading_stack]
            for part in _split_to_cap(body):
                prefix = " › ".join(path_titles)
                chunk_text = f"{prefix}\n{part}"
                sections.append(BrochureSection(
                    heading_path=list(path_titles),
                    text=part,
                    chunk_text=chunk_text,
                    page_start=body_page_start,
                    block_type=_block_type(part),
                    token_count=_count_tokens(chunk_text),
                ))
            body_buf.clear()

        for pi, line in page_lines:
            text = line["text"].strip()
            if not text or _norm(text) in running:
                continue
            level = size_to_level.get(line["size"])
            is_heading = (
                level is not None
                and len(text) <= _MAX_HEADING_CHARS
                and not text.endswith((".", ",", ";"))
            )
            if is_heading:
                flush()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, text))
                body_page_start = pi
            else:
                if not body_buf:
                    body_page_start = pi
                body_buf.append(text)
        flush()

        # ---- pass 5: attach nearest heading + summary to tables -------------
        # nearest section heading that started on or before the table's page
        for t in tables:
            cand = [s for s in sections if s.page_start <= t.page_number]
            t.heading = cand[-1].heading_path[-1] if cand else None
            t.summary = _table_summary(t.page_number, t.heading, t.rows)

        return ParsedBrochure(
            source_file=path,
            page_count=n_pages,
            body_font_size=float(body_size),
            product_name=product_name,
            descriptor=descriptor,
            uin=uins[0] if uins else None,
            uins=uins,
            sections=sections,
            tables=tables,
            full_text=full_text,
        )
