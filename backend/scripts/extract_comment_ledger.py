"""Extract a reviewable comment-evidence ledger + remediation pairs from
docs/downloads.

Signal sources (per scoping decision 2026-06-05):
  1. Word comments + reply threads + reviewer ROLE (names stripped)
  2. Track-changes remediation pairs (w:del -> w:ins, before/after)
  4. Final.docx vs published PDF diffs ("removed before publishing")

Output is review-only files (JSONL + CSV); nothing is written to the
rule/precedent engines here.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import zipfile
from collections import defaultdict
from typing import Dict, List, Optional

from lxml import etree

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
_W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
_NS = {"w": _W, "w14": _W14, "w15": _W15}

# --- reviewer role normalisation -------------------------------------------

# Map a department segment (lower-cased substring match) to a functional role.
# Order matters: first match wins.
_ROLE_RULES = [
    (("legal compliance", "fpu"), "Compliance reviewer"),
    (("underwriting",), "Underwriting reviewer"),
    (("product",), "Product reviewer"),
    (("operations",), "Operations reviewer"),
    (("finance",), "Finance reviewer"),
    (("tax",), "Tax reviewer"),
    (("audit",), "Audit reviewer"),
    (("actuarial",), "Actuarial reviewer"),
    (("marketing",), "Marketing author"),
    (("agency",), "Agency"),
]

# Roles that carry compliance authority — the signal we learn from.
_REVIEWER_ROLES = {
    "Compliance reviewer",
    "Underwriting reviewer",
    "Product reviewer",
    "Operations reviewer",
    "Finance reviewer",
    "Tax reviewer",
    "Audit reviewer",
    "Actuarial reviewer",
}


def normalize_role(author: str) -> str:
    """Reduce a raw author string to a functional role, discarding the name.

    Bajaj authors look like ``Name/Location/Department/LOB`` — the department
    segment drives the role. Authors with no org path are external agencies.
    """
    a = (author or "").strip()
    if "/" not in a:
        return "External author"
    segs = [s.strip() for s in a.split("/")]
    dept = segs[2] if len(segs) >= 3 else segs[-1]
    d = dept.lower()
    for needles, role in _ROLE_RULES:
        if any(n in d for n in needles):
            return role
    return "Other reviewer"


def is_reviewer_role(role: str) -> bool:
    """True when the role carries compliance authority (vs author/agency)."""
    return role in _REVIEWER_ROLES


# --- comment substance filter ----------------------------------------------

# Acknowledgement / status replies that carry no compliance signal. Matched as
# the whole (stripped, lower-cased, de-punctuated) comment, not as substrings.
_ACK_PHRASES = {
    "edited", "apologies", "apology", "ok", "okay", "done", "noted",
    "noted thanks", "thanks", "thank you", "sure", "yes", "no", "fixed",
    "updated", "changed", "removed", "added", "corrected", "resolved",
    "noted thank you", "deleted", "edited as suggested", "made minor changes",
}


def is_substantive_comment(text: str) -> bool:
    """False for empty / acknowledgement-only replies; True for real feedback.

    A bare source URL is substantive (it means "cite this / unsupported claim").
    """
    t = (text or "").strip()
    if not t:
        return False
    # Strip punctuation and collapse whitespace, then match the whole comment
    # against the acknowledgement set. URLs survive as non-ack token salad.
    norm = re.sub(r"[^\w\s]", " ", t).lower()
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm not in _ACK_PHRASES


# --- track-changes remediation pairs ---------------------------------------

def extract_track_change_pairs(document_xml: bytes) -> List[Dict[str, str]]:
    """Per edited paragraph, return {"deleted", "inserted"} from tracked
    changes. Deleted text comes from w:del//w:delText, inserted from
    w:ins//w:t. Paragraphs with no tracked change are skipped."""
    root = etree.fromstring(document_xml)
    pairs: List[Dict[str, str]] = []
    for p in root.iter(f"{{{_W}}}p"):
        deleted = "".join(
            t.text or "" for t in p.findall(".//w:del//w:delText", _NS)
        ).strip()
        inserted = "".join(
            t.text or "" for t in p.findall(".//w:ins//w:t", _NS)
        ).strip()
        if deleted or inserted:
            pairs.append({"deleted": deleted, "inserted": inserted})
    return pairs


# --- comment threads: role + reply linkage + resolution --------------------

def parse_comment_threads(
    comments_xml: bytes, comments_extended_xml: Optional[bytes]
) -> List[Dict]:
    """Parse comments.xml (+ optional commentsExtended.xml) into per-comment
    rows with role, date, comment text, reply linkage and resolution.

    Personal names are discarded — only the normalised ``role`` is kept.
    Reply linkage uses the w14:paraId on each comment's final paragraph and the
    w15:paraIdParent / w15:done attributes in commentsExtended.xml.
    """
    croot = etree.fromstring(comments_xml)

    # comment id -> paraId (last paragraph), and paraId -> comment id
    id_to_paraid: Dict[str, str] = {}
    paraid_to_id: Dict[str, str] = {}
    raw: List[Dict] = []
    for c in croot.findall("w:comment", _NS):
        cid = c.get(f"{{{_W}}}id")
        if cid is None:
            continue
        author = c.get(f"{{{_W}}}author") or ""
        date = c.get(f"{{{_W}}}date") or ""
        body = "".join(t.text or "" for t in c.findall(".//w:t", _NS)).strip()
        para_ids = [
            p.get(f"{{{_W14}}}paraId")
            for p in c.findall(".//w:p", _NS)
            if p.get(f"{{{_W14}}}paraId")
        ]
        para_id = para_ids[-1] if para_ids else None
        if para_id:
            id_to_paraid[cid] = para_id
            paraid_to_id[para_id] = cid
        raw.append(
            {
                "id": cid,
                "role": normalize_role(author),
                "date": date,
                "comment": body,
                "_para_id": para_id,
            }
        )

    # paraId -> (parentParaId, done) from commentsExtended.xml
    parent_of: Dict[str, Optional[str]] = {}
    done_of: Dict[str, bool] = {}
    if comments_extended_xml:
        eroot = etree.fromstring(comments_extended_xml)
        for ex in eroot.findall("w15:commentEx", _NS):
            pid = ex.get(f"{{{_W15}}}paraId")
            if not pid:
                continue
            parent_of[pid] = ex.get(f"{{{_W15}}}paraIdParent")
            done_of[pid] = ex.get(f"{{{_W15}}}done") in ("1", "true")

    rows: List[Dict] = []
    for r in raw:
        pid = r.pop("_para_id")
        parent_para = parent_of.get(pid) if pid else None
        parent_id = paraid_to_id.get(parent_para) if parent_para else None
        rows.append(
            {
                "id": r["id"],
                "role": r["role"],
                "date": r["date"],
                "comment": r["comment"],
                "parent_id": parent_id,
                "is_reply": parent_id is not None,
                "done": done_of.get(pid, False) if pid else False,
            }
        )
    return rows


# --- anchors: the highlighted text each comment is about -------------------

def extract_anchors(document_xml: bytes) -> Dict[str, str]:
    """Map comment id -> the body text spanned by its
    commentRangeStart..commentRangeEnd markers in document.xml."""
    root = etree.fromstring(document_xml)
    open_ranges: Dict[str, List[str]] = {}
    anchors: Dict[str, str] = {}
    for el in root.iter():
        tag = etree.QName(el).localname
        if tag == "commentRangeStart":
            cid = el.get(f"{{{_W}}}id")
            if cid is not None:
                open_ranges[cid] = []
        elif tag == "commentRangeEnd":
            cid = el.get(f"{{{_W}}}id")
            if cid is not None and cid in open_ranges:
                anchors[cid] = "".join(open_ranges.pop(cid)).strip()
        elif tag == "t" and open_ranges:
            txt = el.text or ""
            for buf in open_ranges.values():
                buf.append(txt)
    return anchors


def extract_anchor_contexts(document_xml: bytes) -> Dict[str, str]:
    """Map comment id -> full text of the paragraph(s) its
    commentRangeStart..commentRangeEnd span sits inside.

    Gives each anchored comment its surrounding sentence/paragraph context.
    A comment whose range markers are never both seen is omitted.
    """
    root = etree.fromstring(document_xml)
    # Which comment-range ids are open as we walk each paragraph.
    open_ids: set = set()
    # comment id -> set of paragraph indices it touches
    cid_paras: Dict[str, set] = {}
    para_text: Dict[int, str] = {}

    for p_idx, p in enumerate(root.iter(f"{{{_W}}}p")):
        para_text[p_idx] = "".join(
            t.text or "" for t in p.iter(f"{{{_W}}}t")
        ).strip()
        for el in p.iter():
            tag = etree.QName(el).localname
            if tag == "commentRangeStart":
                cid = el.get(f"{{{_W}}}id")
                if cid is not None:
                    open_ids.add(cid)
                    cid_paras.setdefault(cid, set()).add(p_idx)
            elif tag == "commentRangeEnd":
                cid = el.get(f"{{{_W}}}id")
                if cid is not None and cid in open_ids:
                    cid_paras.setdefault(cid, set()).add(p_idx)
                    open_ids.discard(cid)
        # Any still-open ranges also span this paragraph.
        for cid in open_ids:
            cid_paras.setdefault(cid, set()).add(p_idx)

    out: Dict[str, str] = {}
    for cid, idxs in cid_paras.items():
        joined = " ".join(para_text[i] for i in sorted(idxs) if para_text.get(i))
        if joined:
            out[cid] = joined
    return out


# --- Final.docx vs published PDF -------------------------------------------

def _sentences(text: str) -> List[str]:
    """Naive sentence split on . ! ? boundaries; trims and drops empties."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def removed_sentences(final_text: str, pdf_text: str) -> List[str]:
    """Sentences present in the approved Final.docx but absent from the
    published PDF — i.e. content dropped during final publishing.

    Matching is whitespace-normalised substring containment to tolerate the
    PDF extractor's spacing quirks.
    """
    pdf_norm = re.sub(r"\s+", " ", (pdf_text or "")).lower()
    removed: List[str] = []
    for s in _sentences(final_text):
        needle = re.sub(r"\s+", " ", s).lower()
        if needle and needle not in pdf_norm:
            removed.append(s)
    return removed


# --- regulation / topic tagging --------------------------------------------

# (tag, regex) — surfaces the regulatory hook a comment/anchor touches so the
# rule engine can group findings and trigger deterministic checks.
_REG_PATTERNS = [
    ("Sec 80C", r"\b80\s*c\b|section\s*80c"),
    ("Sec 10(10D)", r"10\s*\(\s*10d\s*\)|section\s*10"),
    ("Sec 80D", r"\b80\s*d\b|section\s*80d"),
    ("GST", r"\bgst\b|goods and services tax"),
    ("Free-look", r"free[\s-]*look"),
    ("Guaranteed-claim", r"guarantee|guaranteed|assured\s+(?:return|income|benefit)"),
    ("Tax-subject-to-change", r"tax laws are subject to change|subject to change"),
    ("Source-citation", r"https?://|source link|as per source"),
    ("IRDAI", r"\birdai\b|\bsebi\b|regulat"),
    ("Returns-projection", r"\b\d+\s*%|\breturns?\b|\bcagr\b"),
]


def detect_regulation_tags(text: str) -> List[str]:
    """Return the regulatory/topic tags surfaced by a comment or anchor."""
    t = (text or "").lower()
    tags: List[str] = []
    for tag, pat in _REG_PATTERNS:
        if re.search(pat, t):
            tags.append(tag)
    return tags


# ===========================================================================
# Orchestration: read docs/downloads, assemble ledger + remediation pairs.
# I/O glue over the tested pure functions above.
# ===========================================================================

logger = logging.getLogger("extract_comment_ledger")

_TICKET_RE = re.compile(r"^(?P<ticket>\d+)_")
_REV_RE = re.compile(r"\((\d+)\)")


def _ticket_of(fname: str) -> Optional[str]:
    m = _TICKET_RE.match(fname)
    return m.group("ticket") if m else None


def _revision_score(fname: str) -> int:
    nums = _REV_RE.findall(fname)
    return max((int(n) for n in nums), default=0)


def _pick_latest(files: List[str]) -> str:
    return max(files, key=lambda f: (_revision_score(f), len(f)))


def _group_by_ticket(downloads_dir: str) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = defaultdict(list)
    for fname in os.listdir(downloads_dir):
        ticket = _ticket_of(fname)
        if ticket:
            groups[ticket].append(fname)
    return groups


def _title_from_fname(fname: str) -> str:
    name = re.sub(r"\.docx$", "", fname, flags=re.IGNORECASE)
    name = re.sub(r"^\d+_", "", name)
    name = name.replace("_Comments_", "", 1).replace("_Final_", "", 1)
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    name = re.sub(r"^\d+[-\s]*", "", name).strip()
    return name


def _read_docx_parts(path: str) -> Dict[str, Optional[bytes]]:
    """Return raw bytes of the docx parts we need (or None if absent)."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        return {
            "document": z.read("word/document.xml") if "word/document.xml" in names else None,
            "comments": z.read("word/comments.xml") if "word/comments.xml" in names else None,
            "extended": z.read("word/commentsExtended.xml") if "word/commentsExtended.xml" in names else None,
        }


def _docx_body_text(document_xml: bytes) -> str:
    root = etree.fromstring(document_xml)
    paras = []
    for p in root.iter(f"{{{_W}}}p"):
        txt = "".join(t.text or "" for t in p.iter(f"{{{_W}}}t")).strip()
        if txt:
            paras.append(txt)
    return "\n".join(paras)


def _pdf_text(path: str) -> str:
    import pdfplumber

    out = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            out.append(page.extract_text() or "")
    return "\n".join(out)


def _is_comments(f: str) -> bool:
    return "_Comments_" in f and f.lower().endswith(".docx")


def _is_final(f: str) -> bool:
    return "_Final_" in f and f.lower().endswith(".docx")


def build_ledger_rows(ticket: str, files: List[str], downloads_dir: str) -> List[Dict]:
    """One row per substantive Word comment for a ticket, with provenance,
    role, the highlighted anchor, reply linkage, resolution and reg tags."""
    comments_files = [f for f in files if _is_comments(f)]
    if not comments_files:
        return []
    chosen = _pick_latest(comments_files)
    parts = _read_docx_parts(os.path.join(downloads_dir, chosen))
    if not parts["comments"] or not parts["document"]:
        return []

    threads = parse_comment_threads(parts["comments"], parts["extended"])
    anchors = extract_anchors(parts["document"])
    contexts = extract_anchor_contexts(parts["document"])
    title = _title_from_fname(chosen)

    rows: List[Dict] = []
    for c in threads:
        if not is_substantive_comment(c["comment"]):
            continue
        anchor = anchors.get(c["id"], "")
        rows.append(
            {
                "ticket": ticket,
                "title": title,
                "source_file": chosen,
                "comment_id": c["id"],
                "highlighted_text": anchor,
                "span_context": contexts.get(c["id"], ""),
                "comment": c["comment"],
                "role": c["role"],
                "is_reviewer": is_reviewer_role(c["role"]),
                "date": c["date"],
                "is_reply": c["is_reply"],
                "parent_comment_id": c["parent_id"],
                "resolved": c["done"],
                "regulation_tags": detect_regulation_tags(c["comment"] + " " + anchor),
            }
        )
    return rows


def build_remediation_rows(ticket: str, files: List[str], downloads_dir: str) -> List[Dict]:
    """Before/after pairs for a ticket: track-changes from the latest Comments
    or Final docx, plus sentences dropped between Final.docx and the PDF."""
    rows: List[Dict] = []

    # Track-changes from the most-edited docx available.
    edited = [f for f in files if _is_comments(f) or _is_final(f)]
    if edited:
        chosen = _pick_latest(edited)
        parts = _read_docx_parts(os.path.join(downloads_dir, chosen))
        if parts["document"]:
            for pair in extract_track_change_pairs(parts["document"]):
                rows.append(
                    {
                        "ticket": ticket,
                        "source_file": chosen,
                        "kind": "track_change",
                        "before": pair["deleted"],
                        "after": pair["inserted"],
                        "regulation_tags": detect_regulation_tags(pair["deleted"] + " " + pair["inserted"]),
                    }
                )

    # Final.docx vs published PDF.
    finals = [f for f in files if _is_final(f)]
    pdfs = [f for f in files if f.lower().endswith(".pdf")]
    if finals and pdfs:
        final_parts = _read_docx_parts(os.path.join(downloads_dir, _pick_latest(finals)))
        if final_parts["document"]:
            final_text = _docx_body_text(final_parts["document"])
            pdf_name = _pick_latest(pdfs)
            try:
                pdf_text = _pdf_text(os.path.join(downloads_dir, pdf_name))
            except Exception as e:  # pragma: no cover - depends on real PDFs
                logger.warning(f"[{ticket}] PDF read failed for {pdf_name}: {e}")
                pdf_text = ""
            for s in removed_sentences(final_text, pdf_text):
                rows.append(
                    {
                        "ticket": ticket,
                        "source_file": pdf_name,
                        "kind": "removed_in_pdf",
                        "before": s,
                        "after": "",
                        "regulation_tags": detect_regulation_tags(s),
                    }
                )
    return rows


def _write_jsonl(path: str, rows: List[Dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_csv(path: str, rows: List[Dict]) -> None:
    if not rows:
        open(path, "w").close()
        return
    fields = list(rows[0].keys())
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            out = {k: ("|".join(v) if isinstance(v, list) else v) for k, v in r.items()}
            w.writerow(out)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--downloads", required=True, help="Path to docs/downloads")
    p.add_argument("--out", required=True, help="Output folder for ledger files")
    p.add_argument("--limit", type=int, default=None, help="Process only N tickets")
    p.add_argument("--no-pdf", action="store_true", help="Skip Final-vs-PDF diffing")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not os.path.isdir(args.downloads):
        raise SystemExit(f"Not a directory: {args.downloads}")
    os.makedirs(args.out, exist_ok=True)

    groups = _group_by_ticket(args.downloads)
    tickets = sorted(groups.keys())
    if args.limit:
        tickets = tickets[: args.limit]

    ledger: List[Dict] = []
    remediation: List[Dict] = []
    for t in tickets:
        try:
            ledger.extend(build_ledger_rows(t, groups[t], args.downloads))
            rem = build_remediation_rows(t, groups[t], args.downloads)
            if args.no_pdf:
                rem = [r for r in rem if r["kind"] != "removed_in_pdf"]
            remediation.extend(rem)
        except Exception as e:
            logger.warning(f"[{t}] failed: {e}")

    _write_jsonl(os.path.join(args.out, "comment_ledger.jsonl"), ledger)
    _write_csv(os.path.join(args.out, "comment_ledger.csv"), ledger)
    _write_jsonl(os.path.join(args.out, "remediation_pairs.jsonl"), remediation)
    _write_csv(os.path.join(args.out, "remediation_pairs.csv"), remediation)

    reviewer_rows = sum(1 for r in ledger if r["is_reviewer"])
    logger.info("=" * 60)
    logger.info(f"Tickets processed     : {len(tickets)}")
    logger.info(f"Ledger comment rows   : {len(ledger)}  (reviewer-authored: {reviewer_rows})")
    logger.info(f"Remediation pairs     : {len(remediation)}")
    logger.info(f"Output written to     : {args.out}")


if __name__ == "__main__":
    main()
