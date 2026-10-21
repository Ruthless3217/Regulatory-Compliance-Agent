"""Stage C — content-first comparison engine + shared change records.

A highlight must be justified by a content difference, never by the structure used to
display the content. Both views (pixel overlay and text redline) are produced from the
same change records.
"""
import pytest

from app.services.pdf_render_service import PositionedWord
from app.services.content_diff_service import SUBSTANTIVE_GROUPS, compare_words, to_overlay, to_blocks

_line_counter = [0]


def para(text, width=8, page=1, block=0, x0=40.0, y0=100.0, cw=5.0, gap=3.0):
    """Lay text out in lines of `width` words (simulating a given column width)."""
    words, line = [], 0
    toks = text.split()
    base = _line_counter[0]
    for i, t in enumerate(toks):
        if i and i % width == 0:
            line += 1
        col = i % width
        x = x0 + col * 40
        words.append(PositionedWord(text=t, page=page, x0=x, y0=y0 + 12 * line, x1=x + cw * len(t), y1=y0 + 12 * line + 10,
                                    line_id=base + line, block_id=block))
    _line_counter[0] = base + line + 1
    return words


def doc(*paras):
    out = []
    for p in paras:
        out += p
    return out


def visible(diff):
    """Wording changes only. Every record is shown by default (Draftable parity); these
    tests pin that layout/typographic/template differences are never *wording* changes."""
    return [c for c in diff.changes if c.filter_group in SUBSTANTIVE_GROUPS]


def marked_texts(words, idxs):
    return [words[i].text for i in idxs]


BODY = ("In case You have made any disclosures to the insurance agent which has not been included in "
        "the Proposal Form You are requested to intimate the Company in writing within 15 days.")


# ---------------------------------------------------------------- formatting-only => nothing
def test_reflow_and_paragraph_split_produce_no_changes():
    old = doc(para(BODY, width=12, block=0))
    half = BODY.split()
    new = doc(para(" ".join(half[:9]), width=5, block=1), para(" ".join(half[9:]), width=7, block=2, y0=200))
    assert visible(compare_words(old, new)) == []


def test_line_end_hyphenation_produces_no_changes():
    new = [PositionedWord("Bajaj", 1, 40, 100, 70, 110, line_id=0), PositionedWord("Life", 1, 75, 100, 95, 110, line_id=0),
           PositionedWord("Accidental", 1, 100, 100, 150, 110, line_id=0), PositionedWord("Per-", 1, 155, 100, 175, 110, line_id=0),
           PositionedWord("manent", 1, 40, 112, 75, 122, line_id=1), PositionedWord("Total/Partial", 1, 80, 112, 140, 122, line_id=1),
           PositionedWord("Dis-", 1, 145, 112, 165, 122, line_id=1),
           PositionedWord("ability", 1, 40, 124, 75, 134, line_id=2), PositionedWord("Benefit", 1, 80, 124, 115, 134, line_id=2),
           PositionedWord("Rider", 1, 120, 124, 145, 134, line_id=2)]
    old = doc(para("Bajaj Life Accidental Permanent Total/Partial Disability Benefit Rider", width=20))
    assert visible(compare_words(old, new)) == []


# ---------------------------------------------------------------- genuine edits, localised
def test_single_word_edit_marks_only_that_word():
    old = doc(para("approach the IRDAI Grievance Cell Centre on the following contact details"))
    new = doc(para("approach the IRDAI Grievance Call Centre on the following contact details", width=4))
    d = compare_words(old, new)
    ch = visible(d)
    assert len(ch) == 1 and ch[0].kind == "modified" and ch[0].category == "content"
    assert marked_texts(old, ch[0].old_words) == ["Cell"]
    assert marked_texts(new, ch[0].new_words) == ["Call"]


def test_suffix_change_detected():
    old = doc(para("You can also register your complaint in the Bima Bharosa portal"))
    new = doc(para("You can also register your complaints in the Bima Bharosa portal"))
    ch = visible(compare_words(old, new))
    assert [(c.old_text, c.new_text) for c in ch] == [("complaint", "complaints")]


@pytest.mark.parametrize("a,b,ctype", [
    ("within 30 days of receipt", "within 15 days of receipt", "numeric_only"),
    ("UIN 116N198V08 applies here", "UIN 116N198V07 applies here", "identifier_only"),
    ("as amended dated 20.03.2015 only", "as amended dated 21.03.2015 only", None),
    ("a refund of 10% is due", "a refund of 12% is due", "numeric_only"),
])
def test_numbers_dates_identifiers_detected(a, b, ctype):
    ch = visible(compare_words(doc(para(a)), doc(para(b))))
    assert len(ch) == 1 and ch[0].category == "content"
    if ctype:
        assert ch[0].change_type == ctype


def test_sentence_insertion_is_only_the_new_words():
    old = doc(para("If You are not satisfied with the response You may approach the Ombudsman."))
    new = doc(para("If You are not satisfied with the response or the complaint is not resolved within 30 days "
                   "You may approach the Ombudsman.", width=6))
    ch = visible(compare_words(old, new))
    assert len(ch) == 1 and ch[0].kind == "added"
    assert ch[0].new_text == "or the complaint is not resolved within 30 days"


def test_sentence_deletion_detected():
    old = doc(para("Nomination may be cancelled. This sentence was removed entirely. Assignment follows."))
    new = doc(para("Nomination may be cancelled. Assignment follows."))
    ch = visible(compare_words(old, new))
    assert len(ch) == 1 and ch[0].kind == "removed" and ch[0].old_text == "This sentence was removed entirely."


def test_moved_clause_with_one_edit():
    a = "Fraud and Misrepresentation The Policy has been issued based on the details provided to us by you"
    b = "Modification Any modifications to the Policy will be carried out by way of an endorsement letter"
    old = doc(para(a, block=0), para(b, block=1, y0=200))
    b_edit = b.replace("endorsement letter", "endorsement notice")
    new = doc(para(b_edit, block=0), para(a, block=1, y0=200))
    d = compare_words(old, new)
    ch = visible(d)
    moves = [c for c in ch if c.kind == "moved"]
    edits = [c for c in ch if c.kind != "moved"]
    assert moves, "identical clause relocated must be a move, not delete+insert"
    assert all(c.move_id for c in moves)
    assert [(e.old_text, e.new_text) for e in edits] == [("letter", "notice")]


def test_repeated_boilerplate_edit_attaches_to_the_right_occurrence():
    clause = "shall be governed by the provisions of Section 39 of the Insurance Act 1938 as amended from time to time"
    old = doc(para("Nomination " + clause, block=0), para("Assignment " + clause, block=1, y0=200))
    new = doc(para("Nomination " + clause, block=0),
              para("Assignment " + clause.replace("Section 39", "Section 38"), block=1, y0=200))
    ch = visible(compare_words(old, new))
    assert len(ch) == 1
    assert ch[0].old_text == "39" and ch[0].new_text == "38"
    # it is the second clause's word
    assert ch[0].old_words[0] > len(clause.split())


# ---------------------------------------------------------------- categories
def test_case_only_change_is_typographic_not_wording():
    d = compare_words(doc(para("the Policy term")), doc(para("the policy term")))
    assert visible(d) == []                                   # not a wording change
    assert [(c.category, c.subtype) for c in d.changes] == [("typographic", "case")]


def test_quote_style_change_is_typographic_not_wording():
    d = compare_words(doc(para("Maharaja's College Ground")), doc(para("Maharaja’s College Ground")))
    assert visible(d) == []                                   # not a wording change
    assert [(c.category, c.subtype) for c in d.changes] == [("typographic", "quote")]


def test_spacing_only_change_is_typographic_not_wording():
    d = compare_words(doc(para("Rider II (UIN: 116B063V01) applies")), doc(para("Rider II (UIN: 116B0 63V01) applies")))
    assert visible(d) == []                                   # not a wording change
    assert [(c.category, c.subtype) for c in d.changes] == [("typographic", "spacing")]


def test_currency_symbol_change_is_typographic_but_visible():
    # potentially material: stays in the default view
    ch = visible(compare_words(doc(para("Sum Assured (Rs.) payable")), doc(para("Sum Assured (₹) payable"))))
    assert [(c.category, c.subtype) for c in ch] == [("typographic", "currency")]


@pytest.mark.parametrize("old_t,new_t", [
    ("Date of Birth <dd/mm/yyyy> Age", "Date of Birth ________ Age"),
    ("Gender < M / F/ T > Date", "Gender Date"),                 # multi-token placeholder removed
    ("Dear <Name of the Policyholder> Sub", "Dear ____________ Sub"),
    ("UIN <116N198V08> here", "UIN 116N198V08 here"),            # placeholder filled
])
def test_placeholders_are_not_wording(old_t, new_t):
    d = compare_words(doc(para(old_t)), doc(para(new_t)))
    assert visible(d) == []
    assert any(c.category == "placeholder" for c in d.changes)


def test_renumbering_is_not_wording_but_clause_deletion_is():
    old = doc(para("14. Deleted clause about assignment text. 15. If the policy lapses revival applies. 16. The Company may call", width=30))
    new = doc(para("14. If the policy lapses revival applies. 15. The Company may call", width=30))
    d = compare_words(old, new)
    ch = visible(d)
    assert len(ch) == 1 and ch[0].kind == "removed"
    assert "Deleted clause about assignment text." in ch[0].old_text
    assert any(c.category == "renumbering" for c in d.changes)


def test_glued_list_marker_split_before_comparison():
    d = compare_words(doc(para("15.If the policy lapses")), doc(para("14. If the policy lapses")))
    assert visible(d) == []
    assert [c.category for c in d.changes] == ["renumbering"]


# ---------------------------------------------------------------- shared records -> both views
def test_overlay_and_text_views_come_from_the_same_records():
    old = doc(para("approach the IRDAI Grievance Cell Centre on the contact details. Ltd is the company."))
    new = doc(para("approach the IRDAI Grievance Call Centre on the contact details. Limited is the company.", width=5))
    d = compare_words(old, new)
    old_marks, new_marks, changes = to_overlay(d)
    blocks = to_blocks(d)
    ov_old = sorted(old[m["index"]].text for m in old_marks)
    ov_new = sorted(new[m["index"]].text for m in new_marks)
    tx_old = sorted(w["text"] for b in blocks if b["type"] == "replace" for w in b["old_words"] if w["changed"])
    tx_new = sorted(w["text"] for b in blocks if b["type"] == "replace" for w in b["new_words"] if w["changed"])
    assert ov_old == tx_old == ["Cell", "Ltd"]
    assert ov_new == tx_new == ["Call", "Limited"]
    assert {c["id"] for c in changes} == {m["change_id"] for m in old_marks + new_marks}


def test_text_blocks_reconstruct_both_documents():
    old = doc(para(BODY))
    new = doc(para(BODY.replace("15 days", "30 days"), width=5))
    blocks = to_blocks(compare_words(old, new))
    def side(b, s):
        if b["type"] == "replace":
            return [w["text"] for w in b[f"{s}_words"]]
        if b["type"] == "equal" or (b["type"] == "delete" and s == "old") or (b["type"] == "insert" and s == "new"):
            return b.get(f"{s}_text", "").split()
        return []
    assert sum((side(b, "old") for b in blocks), []) == BODY.split()
    assert sum((side(b, "new") for b in blocks), []) == BODY.replace("15 days", "30 days").split()


def test_overlay_marks_feed_existing_box_builder():
    from app.services.pdf_render_service import PageMeta
    from app.services.render_orchestrator import _build_pages
    old = doc(para("approach the IRDAI Grievance Cell Centre on the contact details"))
    new = doc(para("approach the IRDAI Grievance Call Centre on the contact details"))
    old_marks, new_marks, _ = to_overlay(compare_words(old, new))
    pages = _build_pages([PageMeta(n=1, w_pt=600, h_pt=800, image_path="p.png")], new, new_marks, "added")
    (box,) = pages[0]["boxes"]
    call = next(w for w in new if w.text == "Call")
    assert (box["x0"], box["y0"], box["x1"], box["y1"]) == (call.x0, call.y0, call.x1, call.y1)


# ---------------------------------------------------------------- real-pair residuals (Stage C round 2)
def test_descriptive_placeholder_matches_the_plain_label():
    # DOCX template "<Name of the Policyholder>" vs PDF label "Name of the Policyholder ______"
    d = compare_words(doc(para("<Name of the Policyholder> Address line")),
                      doc(para("Name of the Policyholder ________ Address line")))
    assert visible(d) == []


def test_placeholder_glued_inside_a_word_is_split_out():
    d = compare_words(doc(para("Date<dd/mm/yyyy>Variant shown here")), doc(para("Date Variant shown here")))
    assert visible(d) == []


def test_dotted_leader_blank_is_a_placeholder():
    d = compare_words(doc(para("In Words: Rupees ………………… Only")), doc(para("In Words: Rupees ________ Only")))
    assert visible(d) == []


def test_lone_punctuation_change_is_typographic():
    d = compare_words(doc(para("PART - A FORWARDING LETTER")), doc(para("Part A FORWARDING LETTER")))
    assert visible(d) == [] and d.changes and all(c.category == "typographic" for c in d.changes)


def test_unique_single_word_relocation_is_a_move():
    old = doc(para("62 Forest Park BHUBANESHWAR 751 009 Tel 0674 Odisha 5 CHANDIGARH Insurance", width=20))
    new = doc(para("62 Odisha Forest Park BHUBANESHWAR 751 009 Tel 0674 5 CHANDIGARH Insurance", width=20))
    ch = visible(compare_words(old, new))
    assert ch and all(c.kind == "moved" for c in ch)


def test_wording_next_to_a_placeholder_is_not_hidden():
    # template salutation removed while the name field stays a blank on both sides
    d = compare_words(doc(para("Dear Mr. / Mrs. / Miss <Name of the Policyholder> Sub")),
                      doc(para("Dear ________________ Sub")))
    ch = visible(d)
    assert [(c.kind, c.category, c.old_text) for c in ch] == [("removed", "content", "Mr. / Mrs. / Miss")]
