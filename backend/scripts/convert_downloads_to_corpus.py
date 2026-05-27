"""Convert docs/downloads/*.docx into dataset_2.1_rl-shaped JSON precedents.

Each ticket family (group of files sharing a numeric prefix) is collapsed to a
single JSON containing:
  - input.draft_text         (body text of the latest Comments_*.docx, sans comments)
  - input.compliance_comments (one line per Word comment: "[author]: body (Context: anchor)")
  - output.final_text        (body text of the latest Final_*.docx if present)
  - metadata.document_id     (the numeric ticket id)
  - metadata.title           (cleaned-up filename of the Comments file)

Skips tickets that have no Comments_*.docx — those have nothing to learn from.

Usage:
  docker exec compliance-backend python -m scripts.convert_downloads_to_corpus \\
      --downloads /app/docs/downloads --out /app/dataset/Dataset/Dataset/dataset_2.1_rl
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import zipfile
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from lxml import etree

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("convert_downloads_to_corpus")

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = NS["w"]

_TICKET_RE = re.compile(r"^(?P<ticket>\d+)_")
_REV_RE = re.compile(r"\((\d+)\)")


def _ticket_of(fname: str) -> Optional[str]:
    m = _TICKET_RE.match(fname)
    return m.group("ticket") if m else None


def _revision_score(fname: str) -> int:
    """Return the highest numeric (N) suffix in the filename, or 0."""
    nums = _REV_RE.findall(fname)
    return max((int(n) for n in nums), default=0)


def _is_comments(fname: str) -> bool:
    return "_Comments_" in fname and fname.lower().endswith(".docx")


def _is_final(fname: str) -> bool:
    return "_Final_" in fname and fname.lower().endswith(".docx")


def _is_excluded_draft(fname: str) -> bool:
    """True if this docx is a side artifact, not a canonical draft."""
    lower = fname.lower()
    if not lower.endswith(".docx"):
        return True
    bad = ("_comments_", "_final_", "_edited", "_re-", "re-", "fw ", "re ")
    return any(b in lower for b in bad)


def _extract_body_text(docx_path: str) -> str:
    """Return plain text of the docx body, paragraphs joined by \\n.

    Skips text inside w:commentRangeStart..End ranges? No — comments are
    referenced via anchor markers but the highlighted text is still part of the
    body, so it stays. We DO skip w:ins and w:del aren't excluded here; track
    changes are kept as-is (rare in this corpus and the LLM tolerates noise).
    """
    with zipfile.ZipFile(docx_path) as z:
        if "word/document.xml" not in z.namelist():
            return ""
        xml = etree.fromstring(z.read("word/document.xml"))

    paragraphs: List[str] = []
    for p in xml.iter(f"{{{W}}}p"):
        runs = [t.text or "" for t in p.iter(f"{{{W}}}t")]
        text = "".join(runs).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _extract_comments(docx_path: str) -> List[Dict[str, str]]:
    """Return [{author, comment, anchor}, …] for every Word comment with a
    non-empty anchor. Reply-comments (anchor missing) are dropped because the
    downstream parser regex requires (Context: …)."""
    with zipfile.ZipFile(docx_path) as z:
        names = set(z.namelist())
        if "word/comments.xml" not in names:
            return []
        doc_xml = etree.fromstring(z.read("word/document.xml"))
        com_xml = etree.fromstring(z.read("word/comments.xml"))

    by_id: Dict[str, Dict[str, str]] = {}
    for c in com_xml.findall("w:comment", NS):
        cid = c.get(f"{{{W}}}id")
        author = (c.get(f"{{{W}}}author") or "").strip()
        body = "".join((t.text or "") for t in c.findall(".//w:t", NS)).strip()
        if cid:
            by_id[cid] = {"author": author, "comment": body, "anchor": ""}

    open_ranges: Dict[str, List[str]] = {}
    for el in doc_xml.iter():
        tag = etree.QName(el).localname
        if tag == "commentRangeStart":
            cid = el.get(f"{{{W}}}id")
            if cid:
                open_ranges[cid] = []
        elif tag == "commentRangeEnd":
            cid = el.get(f"{{{W}}}id")
            if cid and cid in open_ranges:
                anchor = "".join(open_ranges.pop(cid)).strip()
                if cid in by_id:
                    by_id[cid]["anchor"] = anchor
        elif tag == "t" and open_ranges:
            txt = el.text or ""
            for cid in open_ranges:
                open_ranges[cid].append(txt)

    rows: List[Dict[str, str]] = []
    for row in by_id.values():
        if not row["anchor"] or not row["comment"]:
            continue
        rows.append(row)
    return rows


def _sanitize_for_comments_line(s: str) -> str:
    """Make a string safe to embed in a single `(Context: …)` line.

    The downstream parser regex is single-line and uses `]:` and `(Context:` /
    `)` as field delimiters, so we collapse newlines and strip the literal
    `(Context:` and balance-breaking parens from the comment/anchor bodies.
    """
    s = s.replace("\n", " ").replace("\r", " ")
    s = s.replace("(Context:", "(context:")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _comments_to_block(comments: List[Dict[str, str]]) -> str:
    lines = []
    for c in comments:
        author = _sanitize_for_comments_line(c["author"])
        body = _sanitize_for_comments_line(c["comment"])
        anchor = _sanitize_for_comments_line(c["anchor"]).replace(")", "")
        if not (author and body and anchor):
            continue
        lines.append(f"[{author}]: {body} (Context: {anchor})")
    return "\n".join(lines)


def _title_from_comments_fname(fname: str) -> str:
    """Strip ticket prefix, `_Comments_` token, parenthetical revision suffixes,
    extension. Best-effort, just for the metadata.title field."""
    name = re.sub(r"\.docx$", "", fname, flags=re.IGNORECASE)
    name = re.sub(r"^\d+_", "", name)
    name = name.replace("_Comments_", "", 1)
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    name = re.sub(r"^\d+[-\s]*", "", name).strip()
    return name


def _pick_latest(files: List[str]) -> str:
    """Pick the filename with the highest (N) revision score, breaking ties by
    longer name (newer files tend to accumulate suffixes)."""
    return max(files, key=lambda f: (_revision_score(f), len(f)))


def _group_by_ticket(downloads_dir: str) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = defaultdict(list)
    for fname in os.listdir(downloads_dir):
        ticket = _ticket_of(fname)
        if ticket:
            groups[ticket].append(fname)
    return groups


def convert_ticket(
    ticket: str, files: List[str], downloads_dir: str
) -> Optional[Dict]:
    """Return the JSON record for a ticket, or None if it should be skipped."""
    comments_files = [f for f in files if _is_comments(f)]
    if not comments_files:
        return None  # No reviewer signal → skip
    chosen_comments = _pick_latest(comments_files)
    comments_path = os.path.join(downloads_dir, chosen_comments)

    try:
        draft_text = _extract_body_text(comments_path)
        comments_rows = _extract_comments(comments_path)
    except Exception as e:
        logger.warning(f"[{ticket}] failed to parse {chosen_comments}: {e}")
        return None

    if not draft_text or not comments_rows:
        return None

    comments_block = _comments_to_block(comments_rows)
    if not comments_block:
        return None

    final_files = [f for f in files if _is_final(f)]
    final_text = ""
    if final_files:
        try:
            final_text = _extract_body_text(
                os.path.join(downloads_dir, _pick_latest(final_files))
            )
        except Exception as e:
            logger.warning(f"[{ticket}] failed to read Final: {e}")

    return {
        "task": "compliance_review",
        "instruction": "Review insurance marketing copy for SEBI and IRDAI compliance issues.",
        "input": {
            "draft_text": draft_text,
            "compliance_comments": comments_block,
        },
        "output": {
            "final_text": final_text,
        },
        "metadata": {
            "document_id": ticket,
            "title": _title_from_comments_fname(chosen_comments),
        },
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--downloads", required=True, help="Path to docs/downloads")
    p.add_argument("--out", required=True, help="Output folder for JSONs")
    p.add_argument("--limit", type=int, default=None, help="Process only N tickets")
    p.add_argument("--dry-run", action="store_true", help="Don't write JSONs")
    args = p.parse_args()

    if not os.path.isdir(args.downloads):
        sys.exit(f"Not a directory: {args.downloads}")
    os.makedirs(args.out, exist_ok=True)

    groups = _group_by_ticket(args.downloads)
    tickets = sorted(groups.keys())
    if args.limit:
        tickets = tickets[: args.limit]

    written = skipped_no_comments = parse_failed = empty_after_parse = 0
    total_comments = 0
    for ticket in tickets:
        record = convert_ticket(ticket, groups[ticket], args.downloads)
        if record is None:
            if not any(_is_comments(f) for f in groups[ticket]):
                skipped_no_comments += 1
            else:
                empty_after_parse += 1
            continue
        n_comments = record["input"]["compliance_comments"].count("\n") + 1
        total_comments += n_comments
        if not args.dry_run:
            out_path = os.path.join(args.out, f"{ticket}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
        written += 1

    logger.info("=" * 60)
    logger.info(f"Tickets scanned       : {len(tickets)}")
    logger.info(f"JSONs written         : {written}")
    logger.info(f"Skipped (no Comments) : {skipped_no_comments}")
    logger.info(f"Empty after parse     : {empty_after_parse}")
    logger.info(f"Parse failed          : {parse_failed}")
    logger.info(f"Total comment rows    : {total_comments}")
    if args.dry_run:
        logger.info("(dry-run; no files written)")


if __name__ == "__main__":
    main()
