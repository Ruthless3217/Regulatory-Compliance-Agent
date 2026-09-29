"""Structural document alignment and layout-aware comparison service.

Provides structure-first document segmentation, anchor detection, reading-order
resolution, and structural matching for the Compare tool.
Enforces the invariant that changes never cross structural boundaries (e.g. Item 5
cannot incorrectly align or merge with Item 20).
"""
import re
import string
import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import List, Optional, Tuple, Dict, Set, Any

from app.services.comparison_service import (
    _norm_token,
    _is_placeholder,
    _TOKEN_RE,
    _HEADING_MARKER,
    _ends_sentence,
    _emit_blocks,
    detect_moves_in_blocks,
    _detect_moves_in_changes,
)

logger = logging.getLogger(__name__)


@dataclass
class PositionedWord:
    """Word token with page and bounding box coordinates (PDF points, top-left origin)."""
    text: str
    page: int          # 1-based
    x0: float
    y0: float          # top
    x1: float
    y1: float          # bottom
    confidence: float = 1.0
    line_id: int = 0
    col_id: int = 0


@dataclass
class StructuralAnchor:
    """Detected structural marker in a document."""
    anchor_type: str   # "numbered_item" | "heading" | "sub_clause" | "roman" | "unanchored"
    anchor_key: str    # "5", "20", "part_b", "a", "i", etc.
    title: str         # e.g. "Grace Period", "UIN", "Part B - Definitions"
    full_text: str     # e.g. '5. "Grace Period"'


@dataclass
class StructuralRegion:
    """A bounded structural section of a document (definition item, heading, paragraph)."""
    region_id: str
    anchor: StructuralAnchor
    tokens: List[PositionedWord] = field(default_factory=list)
    page_start: int = 1
    page_end: int = 1
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    raw_text: str = ""
    normalized_text: str = ""

    def update_text_and_bbox(self) -> None:
        """Recompute text, normalized string, and bounding box from tokens."""
        if not self.tokens:
            self.raw_text = ""
            self.normalized_text = ""
            self.bbox = (0.0, 0.0, 0.0, 0.0)
            return
        self.raw_text = " ".join(t.text for t in self.tokens)
        self.normalized_text = " ".join(_norm_token(t.text) for t in self.tokens)
        self.page_start = min(t.page for t in self.tokens)
        self.page_end = max(t.page for t in self.tokens)
        self.bbox = (
            min(t.x0 for t in self.tokens),
            min(t.y0 for t in self.tokens),
            max(t.x1 for t in self.tokens),
            max(t.y1 for t in self.tokens),
        )


# ---------------------------------------------------------------------------
# Anchor detection regex patterns
# ---------------------------------------------------------------------------
# Matches: 5. "Grace Period" means..., 20. "UIN" means..., 5. Grace Period: ...
_NUMBERED_ITEM_RE = re.compile(
    r"^\s*(\d+)[\.\)]\s*(?:[\"“'«]([^\"”'»]+)[\"”'»]|([A-Za-z0-9\s/_\-&]+?)(?=\s+means\b|\s+shall\b|\s+is\b|\s+refers\b|\s*:|\.|\n|$))",
    re.IGNORECASE,
)
_GENERIC_NUMBERED_RE = re.compile(r"^\s*(\d+)[\.\)]\s+(.*)")
_SUB_CLAUSE_RE = re.compile(r"^\s*(?:\(([a-z0-9]+)\)|([a-z])[\.\)])\s+(.*)", re.IGNORECASE)
_ROMAN_RE = re.compile(r"^\s*([IVXLCDM]+)[\.\)]\s+(.*)", re.IGNORECASE)
_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(Part\s+[A-Z\d]+|Section\s+\d+|Schedule\s+[A-Z\d]+|Definitions|Benefits|General Conditions|Terms and Conditions|Eligibility|Exclusions|Disclaimers|Grievance Redressal)\b(?:\s*[:\-–—]\s*(.*))?",
    re.IGNORECASE,
)


def detect_anchor(line_text: str) -> Optional[StructuralAnchor]:
    """Detect if a text line introduces a structural anchor."""
    text = line_text.strip()
    if not text:
        return None

    # 1. Numbered definition or item (e.g. 5. "Grace Period", 20. "UIN")
    m_num_item = _NUMBERED_ITEM_RE.match(text)
    if m_num_item:
        num = m_num_item.group(1)
        title = (m_num_item.group(2) or m_num_item.group(3) or "").strip()
        # Clean title of any trailing punctuation
        title = title.strip('":-., ')
        if not title:
            # Fallback to generic numbered line if title wasn't cleanly isolated
            m_gen = _GENERIC_NUMBERED_RE.match(text)
            if m_gen:
                title = m_gen.group(2).strip()[:40]
        return StructuralAnchor(
            anchor_type="numbered_item",
            anchor_key=num,
            title=title or f"Item {num}",
            full_text=m_num_item.group(0).strip(),
        )

    # 2. Generic numbered item (e.g. "1. Policyholder Eligibility")
    m_gen = _GENERIC_NUMBERED_RE.match(text)
    if m_gen:
        num = m_gen.group(1)
        rest = m_gen.group(2).strip()
        title = rest[:40].split("\n")[0]
        return StructuralAnchor(
            anchor_type="numbered_item",
            anchor_key=num,
            title=title or f"Item {num}",
            full_text=f"{num}. {title}",
        )

    # 3. Headings / Parts (e.g. "## Part B - Definitions", "Part B", "Definitions")
    m_head = _HEADING_RE.match(text)
    if m_head:
        main_h = m_head.group(1).strip()
        sub_h = (m_head.group(2) or "").strip()
        full_title = f"{main_h} - {sub_h}" if sub_h else main_h
        key = re.sub(r"[^a-zA-Z0-9]+", "_", main_h.lower()).strip("_")
        return StructuralAnchor(
            anchor_type="heading",
            anchor_key=key,
            title=full_title,
            full_text=m_head.group(0).strip(),
        )

    # 4. Sub-clauses: (a), (b), (1), (2)
    m_sub = _SUB_CLAUSE_RE.match(text)
    if m_sub:
        key = (m_sub.group(1) or m_sub.group(2) or "").lower()
        rest = m_sub.group(3).strip()[:40]
        return StructuralAnchor(
            anchor_type="sub_clause",
            anchor_key=key,
            title=rest or f"({key})",
            full_text=f"({key}) {rest}",
        )

    # 5. Roman numerals: I., II., III.
    m_rom = _ROMAN_RE.match(text)
    if m_rom:
        rom = m_rom.group(1).upper()
        rest = m_rom.group(2).strip()[:40]
        return StructuralAnchor(
            anchor_type="roman",
            anchor_key=rom,
            title=rest or f"{rom}.",
            full_text=f"{rom}. {rest}",
        )

    return None


# ---------------------------------------------------------------------------
# Reading order and Multi-column layout detection
# ---------------------------------------------------------------------------

def detect_reading_order(words: List[PositionedWord], page_w: float = 612.0) -> List[PositionedWord]:
    """Sort words into reading order, handling multi-column layouts where applicable.

    A page is only treated as multi-column if there is a distinct empty vertical gutter
    separating two columns with substantial text on both sides.
    """
    if not words:
        return []

    # Group words by page first
    by_page: Dict[int, List[PositionedWord]] = {}
    for w in words:
        by_page.setdefault(w.page, []).append(w)

    ordered_words: List[PositionedWord] = []

    for page_num in sorted(by_page.keys()):
        page_words = by_page[page_num]
        if len(page_words) < 20:
            sorted_page = sorted(page_words, key=lambda w: (w.y0, w.x0))
            for w in sorted_page:
                w.col_id = 0
            ordered_words.extend(sorted_page)
            continue

        # Look for a vertical gutter separating left and right columns.
        # Test candidate gutter positions between 0.35 * page_w and 0.65 * page_w
        best_gutter = None
        min_crossing = len(page_words)

        for mid_ratio in [0.40, 0.45, 0.50, 0.55, 0.60]:
            mid_x = page_w * mid_ratio
            g_left = mid_x - 12.0
            g_right = mid_x + 12.0

            left_words = [w for w in page_words if w.x1 <= g_left]
            right_words = [w for w in page_words if w.x0 >= g_right]
            crossing_words = [w for w in page_words if not (w.x1 <= g_left or w.x0 >= g_right)]

            # Both columns must have substantial words (> 25% of page each) and almost 0 crossing words
            if (
                len(left_words) >= 10
                and len(right_words) >= 10
                and len(left_words) + len(right_words) >= 0.85 * len(page_words)
                and len(crossing_words) <= max(1, int(len(page_words) * 0.03))
            ):
                if len(crossing_words) < min_crossing:
                    min_crossing = len(crossing_words)
                    best_gutter = (g_left, g_right, left_words, right_words, crossing_words)

        if best_gutter is not None:
            g_left, g_right, left_words, right_words, crossing_words = best_gutter
            col1 = sorted(left_words, key=lambda w: (w.y0, w.x0))
            col2 = sorted(right_words, key=lambda w: (w.y0, w.x0))
            for w in col1:
                w.col_id = 0
            for w in col2:
                w.col_id = 1
            ordered_words.extend(col1)
            # Include crossing words sorted at top/bottom if any
            for w in sorted(crossing_words, key=lambda item: (item.y0, item.x0)):
                w.col_id = 0
                ordered_words.append(w)
            ordered_words.extend(col2)
        else:
            sorted_page = sorted(page_words, key=lambda w: (w.y0, w.x0))
            for w in sorted_page:
                w.col_id = 0
            ordered_words.extend(sorted_page)

    return ordered_words


# ---------------------------------------------------------------------------
# Document Segmentation into Structural Regions
# ---------------------------------------------------------------------------

def _cluster_words_into_lines_with_order(words: List[PositionedWord]) -> List[List[PositionedWord]]:
    """Cluster ordered words into visual text lines."""
    if not words:
        return []
    lines: List[List[PositionedWord]] = []
    for w in words:
        if not lines:
            lines.append([w])
            continue
        last_line = lines[-1]
        line_y0 = min(lw.y0 for lw in last_line)
        line_y1 = max(lw.y1 for lw in last_line)
        overlap = min(w.y1, line_y1) - max(w.y0, line_y0)
        min_h = min(w.y1 - w.y0, line_y1 - line_y0)
        same_page = (w.page == last_line[0].page)
        same_col = (getattr(w, "col_id", 0) == getattr(last_line[0], "col_id", 0))

        if same_page and same_col and ((min_h > 0 and overlap >= 0.4 * min_h) or abs(w.y0 - line_y0) <= 3.5):
            last_line.append(w)
        else:
            lines.append([w])

    for line in lines:
        line.sort(key=lambda item: item.x0)
    return lines


def segment_positioned_words(
    words: List[PositionedWord], page_w: float = 612.0
) -> List[StructuralRegion]:
    """Segment a stream of positioned words into discrete StructuralRegions.

    Identifies numbered items, headings, sub-clauses, and paragraph blocks.
    """
    ordered = detect_reading_order(words, page_w)
    lines = _cluster_words_into_lines_with_order(ordered)

    regions: List[StructuralRegion] = []
    current_anchor: StructuralAnchor = StructuralAnchor(
        anchor_type="unanchored", anchor_key="header", title="Preamble", full_text=""
    )
    current_tokens: List[PositionedWord] = []
    reg_idx = 0

    def flush_region():
        nonlocal current_tokens, reg_idx
        if not current_tokens:
            return
        reg = StructuralRegion(
            region_id=f"reg_{reg_idx}",
            anchor=current_anchor,
            tokens=list(current_tokens),
        )
        reg.update_text_and_bbox()
        regions.append(reg)
        reg_idx += 1
        current_tokens = []

    for line_idx, line in enumerate(lines):
        for w in line:
            w.line_id = line_idx
        line_text = " ".join(w.text for w in line)
        detected = detect_anchor(line_text)

        if detected is not None:
            flush_region()
            current_anchor = detected

        current_tokens.extend(line)

    flush_region()
    return regions


def segment_text_paragraphs(paragraphs: List[str]) -> List[StructuralRegion]:
    """Segment plain text paragraphs into StructuralRegions for text mode diffing."""
    regions: List[StructuralRegion] = []
    current_anchor: StructuralAnchor = StructuralAnchor(
        anchor_type="unanchored", anchor_key="header", title="Preamble", full_text=""
    )
    current_tokens: List[PositionedWord] = []
    reg_idx = 0

    def flush_region():
        nonlocal current_tokens, reg_idx
        if not current_tokens:
            return
        reg = StructuralRegion(
            region_id=f"treg_{reg_idx}",
            anchor=current_anchor,
            tokens=list(current_tokens),
        )
        reg.update_text_and_bbox()
        regions.append(reg)
        reg_idx += 1
        current_tokens = []

    for p_idx, para in enumerate(paragraphs):
        para_clean = _HEADING_MARKER.sub("", para).strip()
        if not para_clean:
            continue
        detected = detect_anchor(para_clean)
        if detected is not None:
            flush_region()
            current_anchor = detected

        for m in _TOKEN_RE.finditer(para_clean):
            t = m.group(0)
            current_tokens.append(
                PositionedWord(
                    text=t,
                    page=1,
                    x0=0.0,
                    y0=float(p_idx * 20),
                    x1=50.0,
                    y1=float(p_idx * 20 + 12),
                    line_id=p_idx,
                )
            )

    flush_region()
    return regions


# ---------------------------------------------------------------------------
# Structural Region Matching
# ---------------------------------------------------------------------------

def _text_similarity(s1: str, s2: str) -> float:
    """Compute normalized token sequence similarity between two texts."""
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    t1 = [_norm_token(w) for w in s1.split()]
    t2 = [_norm_token(w) for w in s2.split()]
    return SequenceMatcher(None, t1, t2, autojunk=False).ratio()


def match_structural_regions(
    old_regions: List[StructuralRegion], new_regions: List[StructuralRegion]
) -> List[Tuple[Optional[StructuralRegion], Optional[StructuralRegion], str]]:
    """Align old and new structural regions hierarchically.

    Priority:
    1. Exact anchor identity + title match (e.g. key="5" and title="Grace Period").
    2. Same title with shifted numbering (e.g. Item 6 GST -> Item 7 GST after insertion).
    3. Exact anchor key match with modified content.
    4. Unanchored sequence similarity.
    5. Move detection for swapped/reordered sections.
    """
    paired_results: List[Tuple[Optional[StructuralRegion], Optional[StructuralRegion], str]] = []
    matched_old_indices: Set[int] = set()
    matched_new_indices: Set[int] = set()

    # Pass 1: Exact Anchor Key AND Title Match
    for oi, o_reg in enumerate(old_regions):
        if o_reg.anchor.anchor_type == "unanchored":
            continue
        best_ni = None
        best_sim = -1.0
        for ni, n_reg in enumerate(new_regions):
            if ni in matched_new_indices:
                continue
            if (
                o_reg.anchor.anchor_type == n_reg.anchor.anchor_type
                and o_reg.anchor.anchor_key == n_reg.anchor.anchor_key
            ):
                title_sim = _text_similarity(o_reg.anchor.title, n_reg.anchor.title)
                body_sim = _text_similarity(o_reg.normalized_text, n_reg.normalized_text)
                sim = max(title_sim, body_sim)
                if title_sim >= 0.45 or sim >= 0.45:
                    if sim > best_sim:
                        best_sim = sim
                        best_ni = ni
        if best_ni is not None:
            matched_old_indices.add(oi)
            matched_new_indices.add(best_ni)
            paired_results.append((o_reg, new_regions[best_ni], "matched"))

    # Pass 2: Same Title with Shifted / Renumbered Anchors (e.g. Item 6 GST -> Item 7 GST)
    for oi, o_reg in enumerate(old_regions):
        if oi in matched_old_indices or o_reg.anchor.anchor_type == "unanchored":
            continue
        best_ni = None
        best_sim = 0.50
        for ni, n_reg in enumerate(new_regions):
            if ni in matched_new_indices or n_reg.anchor.anchor_type == "unanchored":
                continue
            title_sim = _text_similarity(o_reg.anchor.title, n_reg.anchor.title)
            body_sim = _text_similarity(o_reg.normalized_text, n_reg.normalized_text)
            if title_sim >= 0.60 or body_sim >= 0.65:
                sim = max(title_sim, body_sim)
                if sim > best_sim:
                    best_sim = sim
                    best_ni = ni
        if best_ni is not None:
            matched_old_indices.add(oi)
            matched_new_indices.add(best_ni)
            paired_results.append((o_reg, new_regions[best_ni], "matched"))

    # Pass 3: Exact Anchor Key Match with content/spatial evidence (same number edited)
    for oi, o_reg in enumerate(old_regions):
        if oi in matched_old_indices or o_reg.anchor.anchor_type == "unanchored":
            continue
        best_ni = None
        best_score = -1.0
        for ni, n_reg in enumerate(new_regions):
            if ni in matched_new_indices or n_reg.anchor.anchor_type == "unanchored":
                continue
            if (
                o_reg.anchor.anchor_type == n_reg.anchor.anchor_type
                and o_reg.anchor.anchor_key == n_reg.anchor.anchor_key
            ):
                title_sim = _text_similarity(o_reg.anchor.title, n_reg.anchor.title)
                body_sim = _text_similarity(o_reg.normalized_text, n_reg.normalized_text)
                page_dist = abs(o_reg.page_start - n_reg.page_start)
                order_dist = abs(oi - ni)

                # An identical numeric anchor is NOT sufficient on its own:
                # Require title similarity, body similarity, or close page/order proximity.
                valid = (
                    title_sim >= 0.30
                    or body_sim >= 0.30
                    or (page_dist <= 1 and (body_sim >= 0.20 or title_sim >= 0.20))
                    or (order_dist <= 3 and page_dist <= 2 and body_sim >= 0.20)
                )

                if valid:
                    score = max(title_sim, body_sim) - 0.01 * min(page_dist, 10)
                    if score > best_score:
                        best_score = score
                        best_ni = ni

        if best_ni is not None:
            matched_old_indices.add(oi)
            matched_new_indices.add(best_ni)
            paired_results.append((o_reg, new_regions[best_ni], "matched"))

    # Pass 4: Unanchored blocks & Monotonic Sequence Alignment
    remaining_old = [(oi, r) for oi, r in enumerate(old_regions) if oi not in matched_old_indices]
    remaining_new = [(ni, r) for ni, r in enumerate(new_regions) if ni not in matched_new_indices]

    if remaining_old and remaining_new:
        old_texts = [r.normalized_text for _, r in remaining_old]
        new_texts = [r.normalized_text for _, r in remaining_new]
        matcher = SequenceMatcher(None, old_texts, new_texts, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    oi, o_reg = remaining_old[i1 + k]
                    ni, n_reg = remaining_new[j1 + k]
                    matched_old_indices.add(oi)
                    matched_new_indices.add(ni)
                    paired_results.append((o_reg, n_reg, "matched"))
            elif tag == "replace":
                for k_o in range(i1, i2):
                    oi, o_reg = remaining_old[k_o]
                    best_match_j = None
                    best_s = 0.45
                    for k_n in range(j1, j2):
                        ni, n_reg = remaining_new[k_n]
                        if ni in matched_new_indices:
                            continue
                        s = _text_similarity(o_reg.normalized_text, n_reg.normalized_text)
                        if s > best_s:
                            best_s = s
                            best_match_j = ni
                    if best_match_j is not None:
                        matched_old_indices.add(oi)
                        matched_new_indices.add(best_match_j)
                        paired_results.append((o_reg, new_regions[best_match_j], "matched"))

    # Pass 5: Collect unmatched regions as removed / added
    for oi, o_reg in enumerate(old_regions):
        if oi not in matched_old_indices:
            paired_results.append((o_reg, None, "removed"))

    for ni, n_reg in enumerate(new_regions):
        if ni not in matched_new_indices:
            paired_results.append((None, n_reg, "added"))

    # Pass 6: Check for moved sections (out of order matching)
    # If old order and new order of matched pairs are inverted
    matched_indices = [i for i, p in enumerate(paired_results) if p[2] == "matched" and p[0] and p[1]]
    if len(matched_indices) >= 2:
        old_order = [old_regions.index(paired_results[i][0]) for i in matched_indices]
        new_order = [new_regions.index(paired_results[i][1]) for i in matched_indices]
        # Check inversions
        for idx_a in range(len(matched_indices)):
            for idx_b in range(idx_a + 1, len(matched_indices)):
                if (old_order[idx_a] < old_order[idx_b] and new_order[idx_a] > new_order[idx_b]) or (
                    old_order[idx_a] > old_order[idx_b] and new_order[idx_a] < new_order[idx_b]
                ):
                    i = matched_indices[idx_a]
                    j = matched_indices[idx_b]
                    p_i = paired_results[i]
                    p_j = paired_results[j]
                    # If high content identity, tag as moved
                    if _text_similarity(p_i[0].normalized_text, p_i[1].normalized_text) >= 0.8:
                        paired_results[i] = (p_i[0], p_i[1], "moved")
                    if _text_similarity(p_j[0].normalized_text, p_j[1].normalized_text) >= 0.8:
                        paired_results[j] = (p_j[0], p_j[1], "moved")

    # Pass 7: Sort paired results to preserve document flow
    def result_sort_key(item: Tuple[Optional[StructuralRegion], Optional[StructuralRegion], str]):
        o, n, _ = item
        o_page = o.page_start if o else 9999
        o_y = o.bbox[1] if o else 9999
        n_page = n.page_start if n else 9999
        n_y = n.bbox[1] if n else 9999
        return (min(o_page, n_page), min(o_y, n_y))

    paired_results.sort(key=result_sort_key)
    return paired_results


# ---------------------------------------------------------------------------
# Semantic Change Classification Patterns & Logic
# ---------------------------------------------------------------------------

_CURRENCY_SYMBOLS = "₹$€£¥"
_NUMERIC_PATTERN = re.compile(
    r"^(?:(?:rs\.?|inr|usd|[" + _CURRENCY_SYMBOLS + r"])\s*)?-?\d+(?:[,\s]\d{2,3})*(?:\.\d+)?(?:\s*[-–—]?\s*(?:%|cr|crore|lakh|lac|k|m|b|th|days?|months?|years?|hrs?|hours?))?$",
    re.IGNORECASE,
)
_DATE_PATTERN = re.compile(
    r"^(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4})$",
    re.IGNORECASE,
)
_IDENTIFIER_PATTERN = re.compile(
    r"^(?=[A-Za-z0-9/\-_.]*[A-Za-z])(?=[A-Za-z0-9/\-_.]*\d)[A-Za-z0-9/\-_.]{3,}$"
)
_PUNCT_CHARS = set(string.punctuation + "“”‘’«»–—•·…")


def _is_numeric_expression(text: str) -> bool:
    """Check if text is purely a number, currency, percentage, time quantity, or date."""
    cleaned = text.strip().rstrip(".,;:")
    if not cleaned:
        return False
    if _DATE_PATTERN.match(cleaned):
        return True
    if _NUMERIC_PATTERN.match(cleaned):
        return any(c.isdigit() for c in cleaned)
    parts = cleaned.split()
    if len(parts) > 1 and all(_is_numeric_expression(p) for p in parts):
        return True
    return False


def _is_identifier_expression(text: str) -> bool:
    """Check if text is an alphanumeric code, UIN, policy ID, or section identifier."""
    cleaned = text.strip().rstrip(".,;:")
    if not cleaned:
        return False
    if _IDENTIFIER_PATTERN.match(cleaned):
        return True
    return False


def _clean_punctuation(text: str) -> str:
    """Strip punctuation characters from text while preserving word separation."""
    no_punct = "".join(" " if c in _PUNCT_CHARS else c for c in text)
    return " ".join(no_punct.lower().split())


def classify_change(
    old_text: str, new_text: str, kind: str
) -> Tuple[str, Dict[str, Any]]:
    """Deterministically classify textual changes into semantic categories.

    Categories:
    - 'whitespace_only': spacing/wrapping differences with identical word tokens.
    - 'numeric_only': changes between numeric amounts, currencies, percentages, quantities, or dates.
    - 'identifier_only': changes between alphanumeric codes, UINs, or section references.
    - 'punctuation_only': differences only in punctuation marks with identical words.
    - 'reordered': structural or block moves (kind == 'moved').
    - 'insertion': added text within a structural region.
    - 'deletion': removed text within a structural region.
    - 'replacement': substantive text modification or rewrite.
    """
    old_s = old_text.strip()
    new_s = new_text.strip()

    if kind == "moved":
        return "reordered", {"category": "reordered", "reason": "structural_move"}

    if kind == "removed" or (old_s and not new_s):
        return "deletion", {"category": "deletion"}

    if kind == "added" or (new_s and not old_s):
        return "insertion", {"category": "insertion"}

    # kind == "modified" (both old_s and new_s exist)
    # 1. Whitespace only check: token sequences must be identical, differing only in intra-spacing/line-wraps
    if old_s.split() == new_s.split():
        return "whitespace_only", {"category": "whitespace"}

    # 2. Numeric only check: both spans must be numeric/financial/date expressions
    if _is_numeric_expression(old_s) and _is_numeric_expression(new_s):
        old_clean = _clean_punctuation(old_s)
        new_clean = _clean_punctuation(new_s)
        if old_clean and old_clean == new_clean:
            # Disambiguate true decimal vs comma value shift (e.g. 1,000 vs 1.000)
            if (re.match(r"^\d+,\d{3}$", old_s) and re.match(r"^\d+\.\d{3}$", new_s)) or (
                re.match(r"^\d+\.\d{3}$", old_s) and re.match(r"^\d+,\d{3}$", new_s)
            ):
                return "numeric_only", {
                    "category": "numeric",
                    "old_value": old_s,
                    "new_value": new_s,
                }
            return "punctuation_only", {"category": "punctuation", "normalized_text": old_clean}
        return "numeric_only", {
            "category": "numeric",
            "old_value": old_s,
            "new_value": new_s,
        }

    # 3. Identifier only check: both spans must be alphanumeric codes / UINs
    if _is_identifier_expression(old_s) and _is_identifier_expression(new_s):
        return "identifier_only", {
            "category": "identifier",
            "old_id": old_s,
            "new_id": new_s,
        }

    # 4. Punctuation only check: alphanumeric words must be identical
    old_clean = _clean_punctuation(old_s)
    new_clean = _clean_punctuation(new_s)
    if old_clean and old_clean == new_clean:
        return "punctuation_only", {"category": "punctuation", "normalized_text": old_clean}

    # 5. Default to substantive text replacement
    return "replacement", {"category": "substantive_text"}


# ---------------------------------------------------------------------------
# Localized Region-Aware Word-Level Diffing
# ---------------------------------------------------------------------------

def structural_word_level_ops(
    old_words: List[PositionedWord],
    new_words: List[PositionedWord],
    page_w: float = 612.0,
) -> Tuple[List[dict], List[dict], List[dict]]:
    """Execute structural document alignment and localized word-level diffing.

    Guarantees that changes never cross structural boundaries (e.g. Item 5 and Item 20).
    Returns (old_marks, new_marks, changes).
    """
    old_regions = segment_positioned_words(old_words, page_w)
    new_regions = segment_positioned_words(new_words, page_w)

    pairs = match_structural_regions(old_regions, new_regions)

    old_marks: List[dict] = []
    new_marks: List[dict] = []
    changes: List[dict] = []
    counter = 0

    # Build global word index lookup: id(word) -> global_index
    old_word_to_idx = {id(w): i for i, w in enumerate(old_words)}
    new_word_to_idx = {id(w): i for i, w in enumerate(new_words)}

    def _all_ph(texts):
        return bool(texts) and all(_is_placeholder(t) for t in texts)

    for o_reg, n_reg, status in pairs:
        if status == "removed" and o_reg:
            old_span = [w.text for w in o_reg.tokens]
            if _all_ph(old_span):
                continue
            cid = f"r{counter}"
            counter += 1
            for w in o_reg.tokens:
                g_idx = old_word_to_idx.get(id(w))
                if g_idx is not None:
                    old_marks.append({"index": g_idx, "type": "removed", "change_id": cid})
            old_str = " ".join(old_span)
            c_type, c_meta = classify_change(old_str, "", "removed")
            changes.append({
                "id": cid,
                "kind": "removed",
                "change_type": c_type,
                "metadata": c_meta,
                "old_text": old_str,
                "new_text": "",
                "structure": {
                    "anchor_type": o_reg.anchor.anchor_type,
                    "anchor_key": o_reg.anchor.anchor_key,
                    "title": o_reg.anchor.title,
                },
            })

        elif status == "added" and n_reg:
            new_span = [w.text for w in n_reg.tokens]
            if _all_ph(new_span):
                continue
            cid = f"r{counter}"
            counter += 1
            for w in n_reg.tokens:
                g_idx = new_word_to_idx.get(id(w))
                if g_idx is not None:
                    new_marks.append({"index": g_idx, "type": "added", "change_id": cid})
            new_str = " ".join(new_span)
            c_type, c_meta = classify_change("", new_str, "added")
            changes.append({
                "id": cid,
                "kind": "added",
                "change_type": c_type,
                "metadata": c_meta,
                "old_text": "",
                "new_text": new_str,
                "structure": {
                    "anchor_type": n_reg.anchor.anchor_type,
                    "anchor_key": n_reg.anchor.anchor_key,
                    "title": n_reg.anchor.title,
                },
            })

        elif status == "moved" and o_reg and n_reg:
            cid = f"r{counter}"
            counter += 1
            for w in o_reg.tokens:
                g_idx = old_word_to_idx.get(id(w))
                if g_idx is not None:
                    old_marks.append({"index": g_idx, "type": "removed", "change_id": cid})
            for w in n_reg.tokens:
                g_idx = new_word_to_idx.get(id(w))
                if g_idx is not None:
                    new_marks.append({"index": g_idx, "type": "added", "change_id": cid})
            old_str = " ".join(w.text for w in o_reg.tokens)
            new_str = " ".join(w.text for w in n_reg.tokens)
            c_type, c_meta = classify_change(old_str, new_str, "moved")
            changes.append({
                "id": cid,
                "kind": "moved",
                "change_type": c_type,
                "metadata": c_meta,
                "old_text": old_str,
                "new_text": new_str,
                "structure": {
                    "anchor_type": o_reg.anchor.anchor_type,
                    "anchor_key": o_reg.anchor.anchor_key,
                    "title": o_reg.anchor.title,
                },
            })

        elif status == "matched" and o_reg and n_reg:
            o_texts = [w.text for w in o_reg.tokens]
            n_texts = [w.text for w in n_reg.tokens]
            o_norm = [_norm_token(t) for t in o_texts]
            n_norm = [_norm_token(t) for t in n_texts]

            is_renumbered = (
                o_reg.anchor.anchor_type == "numbered_item"
                and n_reg.anchor.anchor_type == "numbered_item"
                and o_reg.anchor.anchor_key != n_reg.anchor.anchor_key
            )

            # Run localized SequenceMatcher ONLY within this matched region pair!
            matcher = SequenceMatcher(None, o_norm, n_norm, autojunk=False)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    continue

                # If definition anchor shifted numbering due to insertion/deletion elsewhere,
                # suppress the change on the number prefix token itself so unmodified
                # definition bodies produce zero changes.
                if is_renumbered and i1 == 0 and j1 == 0 and i2 == 1 and j2 == 1:
                    o_k = _norm_token(o_texts[0])
                    n_k = _norm_token(n_texts[0])
                    if o_k == o_reg.anchor.anchor_key and n_k == n_reg.anchor.anchor_key:
                        continue

                old_sub = o_texts[i1:i2]
                new_sub = n_texts[j1:j2]
                if _all_ph(old_sub) or _all_ph(new_sub):
                    continue

                cid = f"r{counter}"
                counter += 1
                if tag == "delete":
                    kind, otype, ntype = "removed", "removed", None
                elif tag == "insert":
                    kind, otype, ntype = "added", None, "added"
                else:  # replace
                    kind, otype, ntype = "modified", "changed", "changed"

                for k in range(i1, i2):
                    g_idx = old_word_to_idx.get(id(o_reg.tokens[k]))
                    if g_idx is not None:
                        old_marks.append({"index": g_idx, "type": otype, "change_id": cid})

                for k in range(j1, j2):
                    g_idx = new_word_to_idx.get(id(n_reg.tokens[k]))
                    if g_idx is not None:
                        new_marks.append({"index": g_idx, "type": ntype, "change_id": cid})

                old_str = " ".join(old_sub)
                new_str = " ".join(new_sub)
                c_type, c_meta = classify_change(old_str, new_str, kind)
                changes.append({
                    "id": cid,
                    "kind": kind,
                    "change_type": c_type,
                    "metadata": c_meta,
                    "old_text": old_str,
                    "new_text": new_str,
                    "structure": {
                        "anchor_type": o_reg.anchor.anchor_type,
                        "anchor_key": o_reg.anchor.anchor_key,
                        "title": o_reg.anchor.title,
                    },
                })

    _detect_moves_in_changes(new_marks, changes)
    for c in changes:
        if c.get("kind") == "moved" and c.get("change_type") != "reordered":
            c["change_type"] = "reordered"
            c["metadata"] = {"category": "reordered", "reason": "block_move"}

    return old_marks, new_marks, changes


def structural_build_diff(
    old_paragraphs: List[str], new_paragraphs: List[str]
) -> List[dict]:
    """Execute structural document alignment and produce localized DiffBlocks.

    Prevents cross-region alignment errors in text redline view.
    """
    old_regions = segment_text_paragraphs(old_paragraphs)
    new_regions = segment_text_paragraphs(new_paragraphs)

    pairs = match_structural_regions(old_regions, new_regions)
    blocks: List[dict] = []
    move_counter = 0

    for o_reg, n_reg, status in pairs:
        if status == "removed" and o_reg:
            old_words = [{"text": w.text, "changed": True} for w in o_reg.tokens]
            blocks.extend(_emit_blocks(old_words, []))
        elif status == "added" and n_reg:
            new_words = [{"text": w.text, "changed": True} for w in n_reg.tokens]
            blocks.extend(_emit_blocks([], new_words))
        elif status == "moved" and o_reg and n_reg:
            mid = f"m{move_counter}"
            move_counter += 1
            blocks.append({
                "type": "delete",
                "old_text": o_reg.raw_text,
                "moved": True,
                "move_id": mid,
            })
            blocks.append({
                "type": "insert",
                "new_text": n_reg.raw_text,
                "moved": True,
                "move_id": mid,
            })
        elif status == "matched" and o_reg and n_reg:
            o_texts = [w.text for w in o_reg.tokens]
            n_texts = [w.text for w in n_reg.tokens]
            o_norm = [_norm_token(t) for t in o_texts]
            n_norm = [_norm_token(t) for t in n_texts]

            is_renumbered = (
                o_reg.anchor.anchor_type == "numbered_item"
                and n_reg.anchor.anchor_type == "numbered_item"
                and o_reg.anchor.anchor_key != n_reg.anchor.anchor_key
            )

            matcher = SequenceMatcher(None, o_norm, n_norm, autojunk=False)
            cur_old: List[dict] = []
            cur_new: List[dict] = []

            def flush():
                nonlocal cur_old, cur_new
                if cur_old or cur_new:
                    blocks.extend(_emit_blocks(cur_old, cur_new))
                    cur_old, cur_new = [], []

            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    for k in range(i2 - i1):
                        ot, nt = o_texts[i1 + k], n_texts[j1 + k]
                        cur_old.append({"text": ot, "changed": False})
                        cur_new.append({"text": nt, "changed": False})
                        if _ends_sentence(ot) or _ends_sentence(nt):
                            flush()
                elif tag == "replace":
                    if is_renumbered and i1 == 0 and j1 == 0 and i2 == 1 and j2 == 1:
                        o_k = _norm_token(o_texts[0])
                        n_k = _norm_token(n_texts[0])
                        if o_k == o_reg.anchor.anchor_key and n_k == n_reg.anchor.anchor_key:
                            cur_old.append({"text": o_texts[0], "changed": False})
                            cur_new.append({"text": n_texts[0], "changed": False})
                            continue
                    for ot in o_texts[i1:i2]:
                        cur_old.append({"text": ot, "changed": True})
                    for nt in n_texts[j1:j2]:
                        cur_new.append({"text": nt, "changed": True})
                elif tag == "delete":
                    for ot in o_texts[i1:i2]:
                        cur_old.append({"text": ot, "changed": True})
                        if not cur_new and _ends_sentence(ot):
                            flush()
                elif tag == "insert":
                    for nt in n_texts[j1:j2]:
                        cur_new.append({"text": nt, "changed": True})
                        if not cur_old and _ends_sentence(nt):
                            flush()
            flush()

    return detect_moves_in_blocks(blocks)
