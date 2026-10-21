"""Accept all tracked changes in a DOCX so it can be compared in its final state.

LibreOffice (our Gotenberg DOCX->PDF route) renders unaccepted revisions as visible
markup — deleted text struck through, deleted table rows still present — so the pixel
compare would diff against text the author already removed. The text extractor
already reads the accepted view (it skips ``w:delText``); this brings the rendered
view in line with it.

Pure OOXML rewrite on a copy (zip + lxml); the upload itself is never modified.
"""
import shutil
import zipfile
from typing import Iterable

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _q(tag: str) -> str:
    return "{%s}%s" % (W, tag)


# Containers whose *content* is deleted text (or the source of a move): drop entirely.
_DROP = {_q("del"), _q("moveFrom")}
# Containers whose content is accepted text: keep children, drop the wrapper.
_UNWRAP = {_q("ins"), _q("moveTo")}
# Revision bookkeeping / formatting-change history: drop (current formatting stays).
_DROP_MARKERS = {
    _q(t) for t in (
        "rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "tblPrExChange",
        "trPrChange", "tcPrChange", "tblGridChange", "numberingChange",
        "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd",
        "cellIns", "customXmlInsRangeStart", "customXmlInsRangeEnd",
        "customXmlDelRangeStart", "customXmlDelRangeEnd",
        "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd",
        "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd",
    )
}
_REVISION_TAGS = _DROP | _UNWRAP | {_q("delText"), _q("cellDel")} | _DROP_MARKERS

_PARTS_PREFIXES = ("word/document", "word/header", "word/footer", "word/footnotes", "word/endnotes", "word/comments")


def _is_content_part(name: str) -> bool:
    return name.startswith(_PARTS_PREFIXES) and name.endswith(".xml")


def _accept(root: etree._Element) -> bool:
    changed = False
    # 1) table rows / cells deleted as a whole
    for tr in list(root.iter(_q("tr"))):
        trPr = tr.find(_q("trPr"))
        if trPr is not None and trPr.find(_q("del")) is not None:
            tr.getparent().remove(tr); changed = True
    for tc in list(root.iter(_q("tc"))):
        tcPr = tc.find(_q("tcPr"))
        if tcPr is not None and tcPr.find(_q("cellDel")) is not None:
            tc.getparent().remove(tc); changed = True
    # 2) paragraph-mark / run-property revision markers (w:rPr/w:ins, w:rPr/w:del, w:trPr/w:ins)
    for holder in ("rPr", "trPr"):
        for pr in list(root.iter(_q(holder))):
            for m in list(pr):
                if m.tag in (_q("ins"), _q("del")):
                    pr.remove(m); changed = True
    # 3) bookkeeping markers
    for el in [e for e in root.iter() if e.tag in _DROP_MARKERS]:
        el.getparent().remove(el); changed = True
    # 4) deleted / moved-from content
    for el in [e for e in root.iter() if e.tag in _DROP]:
        parent = el.getparent()
        if parent is not None:
            parent.remove(el); changed = True
    # 5) inserted / moved-to content: unwrap, preserving order
    for el in [e for e in root.iter() if e.tag in _UNWRAP]:
        parent = el.getparent()
        if parent is None:
            continue
        idx = parent.index(el)
        for child in list(el):
            parent.insert(idx, child); idx += 1
        parent.remove(el); changed = True
    return changed


def _xml_parts_with_revisions(z: zipfile.ZipFile) -> Iterable[str]:
    for name in z.namelist():
        if not _is_content_part(name):
            continue
        root = etree.fromstring(z.read(name))
        if any(e.tag in _REVISION_TAGS for e in root.iter()):
            yield name


def has_revisions(path: str) -> bool:
    """True if any content part still carries tracked-change markup."""
    with zipfile.ZipFile(path) as z:
        return next(iter(_xml_parts_with_revisions(z)), None) is not None


def accept_all_revisions(src: str, dst: str) -> str:
    """Write ``dst`` = ``src`` with every tracked change accepted and tracking switched off.

    Returns ``dst``. A document with no revisions is copied unchanged.
    """
    with zipfile.ZipFile(src) as zin:
        targets = set(_xml_parts_with_revisions(zin))
        settings_has_tracking = (
            "word/settings.xml" in zin.namelist()
            and b"trackRevisions" in zin.read("word/settings.xml")
        )
        if not targets and not settings_has_tracking:
            shutil.copyfile(src, dst)
            return dst
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename in targets:
                    root = etree.fromstring(data)
                    _accept(root)
                    data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
                elif item.filename == "word/settings.xml" and settings_has_tracking:
                    root = etree.fromstring(data)
                    for el in list(root.iter(_q("trackRevisions"))):
                        el.getparent().remove(el)
                    data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
                zout.writestr(item, data)
    return dst
