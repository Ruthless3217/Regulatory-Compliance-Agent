"""Throwaway: pretty-print Word comments + their anchors from a docx for inspection."""
from __future__ import annotations
import sys, zipfile
from lxml import etree

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = NS["w"]


def extract(path: str):
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "word/comments.xml" not in names:
            return [], ""
        doc_xml = etree.fromstring(z.read("word/document.xml"))
        com_xml = etree.fromstring(z.read("word/comments.xml"))

    by_id = {}
    for c in com_xml.findall("w:comment", NS):
        cid = c.get(f"{{{W}}}id")
        body = "".join((t.text or "") for t in c.findall(".//w:t", NS)).strip()
        by_id[cid] = {
            "id": cid,
            "author": (c.get(f"{{{W}}}author") or "").strip(),
            "date": (c.get(f"{{{W}}}date") or "").strip(),
            "comment": body,
            "anchor": "",
        }

    open_ranges = {}
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

    paragraphs = []
    for p in doc_xml.iter(f"{{{W}}}p"):
        runs = [t.text or "" for t in p.iter(f"{{{W}}}t")]
        text = "".join(runs).strip()
        if text:
            paragraphs.append(text)
    body = "\n".join(paragraphs)
    return list(by_id.values()), body


if __name__ == "__main__":
    path = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    comments, body = extract(path)
    print(f"=== {path} ===")
    print(f"body_length={len(body)}, paragraphs≈{body.count(chr(10))+1}, comments={len(comments)}")
    print(f"--- first {n} comments ---")
    for i, c in enumerate(comments[:n], 1):
        print(f"[{i}] author={c['author']!r}")
        print(f"     anchor : {c['anchor'][:140]}")
        print(f"     comment: {c['comment'][:240]}")
        print()
