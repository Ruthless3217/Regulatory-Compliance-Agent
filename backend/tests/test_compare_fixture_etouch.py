"""Real-pair checkpoints (eTouch II DOCX vs PDF) for Stage A (source normalisation +
reading order) and Stage B (word reconstruction).

The policy documents are gitignored and never committed: tests skip when absent.
DOCX-render checks also need the Gotenberg sidecar (COMPARE_GOTENBERG_URL, default
http://localhost:3033) and skip when it is unreachable.
"""
import os
from difflib import SequenceMatcher
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
CANDIDATES = [Path(os.environ["COMPARE_FIXTURE_DIR"])] if os.environ.get("COMPARE_FIXTURE_DIR") else []
CANDIDATES += [HERE.parents[2], HERE.parents[4] if len(HERE.parents) > 4 else HERE.parents[2]]
DOCX_NAME = "eTouch II - Ann H - Policy Document 20260708.docx"
PDF_NAME = "eTouch II_PD_V08.pdf"
FIX = next((d for d in CANDIDATES if (d / PDF_NAME).exists() and (d / DOCX_NAME).exists()), None)
pytestmark = pytest.mark.skipif(FIX is None, reason="eTouch II fixture pair not present (gitignored)")

GOTENBERG = os.environ.get("COMPARE_GOTENBERG_URL", "http://localhost:3033")


def _gotenberg_up():
    try:
        import httpx
        return httpx.get(GOTENBERG + "/health", timeout=3).status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def new_words():
    from app.services.pdf_render_service import positioned_words
    return positioned_words(str(FIX / PDF_NAME))


@pytest.fixture(scope="module")
def old_words(tmp_path_factory):
    if not _gotenberg_up():
        pytest.skip("Gotenberg not reachable")
    from app.config import settings
    from app.services.pdf_render_service import to_pdf, positioned_words
    prev = settings.gotenberg_url
    settings.gotenberg_url = GOTENBERG
    try:
        pdf = to_pdf(str(FIX / DOCX_NAME), "docx", str(tmp_path_factory.mktemp("render")), "old")
    finally:
        settings.gotenberg_url = prev
    return positioned_words(pdf)


def _page_text(words, page):
    return " ".join(w.text for w in words if w.page == page)


# ------------------------------------------------------------------ Stage A
def test_two_column_page_reads_column_by_column(new_words):
    p13 = _page_text(new_words, 13)
    assert "Customer Care Center at Branch Office of the Company" in p13
    assert "Nomination and Assignment: Nomination would be governed by the provisions of" in p13


def test_table_cells_are_not_interleaved(new_words):
    p4 = _page_text(new_words, 4)
    assert "Name of the Rider Life Assured" in p4
    assert "Bajaj Life New Critical Ill- ness Benefit Rider" in p4


def test_rupee_glyph_is_read_as_rupee(new_words):
    texts = [w.text for w in new_words]
    assert not any("`" in t for t in texts)
    assert any("₹" in t for t in texts)


def test_accepted_docx_render_has_no_tracked_deletions(old_words):
    text = " ".join(w.text for w in old_words)
    # rows/tables deleted under track changes (visible only in the un-accepted render)
    assert "Specification of Documents provided" not in text
    assert "116B034V02" not in text  # deleted ADB rider row UIN


def test_docx_render_footer_with_file_name_and_page_counter_is_stripped(old_words):
    text = " ".join(w.text for w in old_words)
    assert ".docx" not in text
    assert "Page 3 of" not in text


# ------------------------------------------------------------------ Stage B
def _keys(old_words, new_words):
    from app.services.compare_tokens import build_tokens, reconstruct_words, vocabulary
    ov, nv = vocabulary(build_tokens(old_words)), vocabulary(build_tokens(new_words))
    return reconstruct_words(old_words, nv), reconstruct_words(new_words, ov)


@pytest.mark.parametrize("word", ["illness", "permanent", "disability", "ombudsman", "commencement", "premium", "instalment"])
def test_pdf_table_hyphenations_reconstructed(old_words, new_words, word):
    _, nt = _keys(old_words, new_words)
    keys = [t.key.rstrip(",.") for t in nt]
    assert word in keys
    assert not {"ill-", "per-", "dis-", "om-"} & set(keys)


def test_stage_ab_alignment_and_genuine_changes_preserved(old_words, new_words):
    ot, nt = _keys(old_words, new_words)
    ok, nk = [t.key for t in ot], [t.key for t in nt]
    sm = SequenceMatcher(None, ok, nk, autojunk=False)
    equal = sum(i2 - i1 for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag == "equal")
    ratio = equal / len(ok)
    assert ratio >= 0.93, ratio

    changed_old, changed_new = set(), set()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            changed_old.update(range(i1, i2)); changed_new.update(range(j1, j2))

    def at(keys, window, target):
        w = window.split()
        hits = [i + w.index(target) for i in range(len(keys) - len(w) + 1) if keys[i:i + len(w)] == w]
        assert hits, f"context not found: {window!r}"
        return hits

    # genuine differences from the reviewed reference set must remain differences
    # at their own position (not merely somewhere in the document)
    old_checks = [("irdai grievance cell centre", "cell"),                      # G10
                  ("register your complaint in", "complaint"),                   # G11 (corrected)
                  ("at http://www.coins.co.in/ombudsman", "http://www.coins.co.in/ombudsman"),  # G16
                  ("insurance laws amendment", "laws"),                          # G21
                  ("care centre at", "centre")]                                  # G05
    new_checks = [("irdai grievance call centre", "call"),
                  ("register your complaints in", "complaints"),
                  ("at http://www.cioins.co.in/ombudsman", "http://www.cioins.co.in/ombudsman"),
                  ("care center at", "center")]
    for window, target in old_checks:
        assert all(i in changed_old for i in at(ok, window, target)), window
    for window, target in new_checks:
        assert all(i in changed_new for i in at(nk, window, target)), window
    rupee50 = [i for i, k in enumerate(nk) if k == "₹50"]                       # G28
    assert rupee50 and all(i in changed_new for i in rupee50)
