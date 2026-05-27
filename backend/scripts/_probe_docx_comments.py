"""One-off probe: extract Word comments + anchors from a single docx.

Not part of the production pipeline — kept for reference; safe to delete.
"""
from __future__ import annotations

import sys
import zipfile
from lxml import etree

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


def extract_comments_with_anchors(path: str) -> list[dict]:
    """Return [{id, author, date, comment, anchor}] for every comment in a docx."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "word/comments.xml" not in names:
            return []
        doc_xml = etree.fromstring(z.read("word/document.xml"))
        com_xml = etree.fromstring(z.read("word/comments.xml"))

    # 1. Comment metadata + text body
    comments: dict[str, dict] = {}
    for c in com_xml.findall("w:comment", NS):
        cid = c.get(f"{{{NS['w']}}}id")
        author = c.get(f"{{{NS['w']}}}author") or ""
        date = c.get(f"{{{NS['w']}}}date") or ""
        # Concatenate all text runs in the comment body
        text_runs = c.findall(".//w:t", NS)
        body = "".join(t.text or "" for t in text_runs).strip()
        comments[cid] = {"id": cid, "author": author, "date": date, "comment": body, "anchor": ""}

    # 2. Walk document.xml in order; for each commentRangeStart/End pair,
    # collect the w:t text between them as the anchor.
    current_open: dict[str, list[str]] = {}  # cid -> [text fragments]
    for el in doc_xml.iter():
        tag = etree.QName(el).localname
        if tag == "commentRangeStart":
            cid = el.get(f"{{{NS['w']}}}id")
            if cid:
                current_open[cid] = []
        elif tag == "commentRangeEnd":
            cid = el.get(f"{{{NS['w']}}}id")
            if cid and cid in current_open:
                anchor = "".join(current_open.pop(cid)).strip()
                if cid in comments:
                    comments[cid]["anchor"] = anchor
        elif tag == "t" and current_open:
            txt = el.text or ""
            for cid in current_open:
                current_open[cid].append(txt)

    return list(comments.values())


if __name__ == "__main__":
    path = sys.argv[1]
    rows = extract_comments_with_anchors(path)
    print(f"Found {len(rows)} comments in {path}")
    for r in rows[:5]:
        print("---")
        print(f"  author : {r['author']}")
        print(f"  date   : {r['date']}")
        print(f"  comment: {r['comment'][:140]}")
        print(f"  anchor : {r['anchor'][:140]}")
