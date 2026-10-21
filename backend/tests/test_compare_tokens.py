"""Stage A (normalisation) + Stage B (word reconstruction) for comparison tokens.

Comparison keys are for MATCHING only; the source text and source word indices are
preserved on every token so highlights can be projected back onto the original boxes.
"""
import pytest

from app.services.pdf_render_service import PositionedWord
from app.services.compare_tokens import (
    comparison_key,
    build_tokens,
    reconstruct_words,
    vocabulary,
)


# ---------------------------------------------------------------- Stage A: keys
@pytest.mark.parametrize("a,b", [
    ("Policyholder’s", "Policyholder's"),       # curly vs straight apostrophe
    ("“Grace", '"Grace'),                        # curly vs straight quote
    ("ﬁnal", "final"),                           # ligature (NFKC)
    ("non­payment", "nonpayment"),          # soft hyphen is invisible
    ("Life​", "Life"),                      # zero-width space
    ("Premium,", "premium"),                     # edge punctuation + case for matching
    ("<dd/mm/yyyy>", "<XX>"),                    # placeholders share a wildcard
])
def test_equivalent_forms_share_a_key(a, b):
    assert comparison_key(a) == comparison_key(b)


@pytest.mark.parametrize("a,b", [
    ("premium", "premiums"),
    ("116N198V08", "116N198V07"),
    ("cell", "call"),
    ("Laws", "Law"),
    ("20.03.2015", "2015"),
    ("10%", "12%"),
    ("₹50", "50"),          # currency symbol is content, not punctuation
    ("non-payment", "nonpayment"),
    ("coins.co.in", "cioins.co.in"),
])
def test_genuine_differences_keep_distinct_keys(a, b):
    assert comparison_key(a) != comparison_key(b)


def test_rupee_font_backtick_is_read_as_rupee():
    from app.services.pdf_render_service import _font_normalized_text
    assert _font_normalized_text("`50", "EOBVOG+RupeeForadian") == "₹50"
    assert _font_normalized_text("(`)", "EOBVOG+Rupee") == "(₹)"
    assert _font_normalized_text("`quoted`", "Helvetica") == "`quoted`"


# ---------------------------------------------------------------- Stage B: reconstruction
def W(text, x0, y0, line_id, block_id=0, page=1, cw=5.0):
    return PositionedWord(text=text, page=page, x0=x0, y0=y0, x1=x0 + cw * len(text), y1=y0 + 10,
                          line_id=line_id, block_id=block_id)


def keys(tokens):
    return [t.key for t in tokens]


def test_line_end_hyphen_rejoined_into_one_token_with_both_sources():
    ws = [W("Accidental", 10, 100, 0), W("Per-", 70, 100, 0), W("manent", 10, 112, 1), W("Total", 50, 112, 1)]
    toks = reconstruct_words(ws, other_vocab={"accidental", "permanent", "total"})
    assert keys(toks) == ["accidental", "permanent", "total"]
    joined = toks[1]
    assert joined.src == (1, 2) and joined.reason == "hyphen-join"
    assert joined.text == "Per-manent"          # original text preserved for display/audit


@pytest.mark.parametrize("parts,expected", [
    (["Criti-", "cal", "Ill-", "ness"], ["critical", "illness"]),
    (["Om-", "budsman,"], ["ombudsman"]),
    (["HYDER-", "ABAD"], ["hyderabad"]),
    (["BHU-", "BANESH-", "WAR"], ["bhubaneshwar"]),          # one word over three lines
])
def test_multiple_line_breaks(parts, expected):
    ws = [W(p, 10, 100 + 12 * i, i) for i, p in enumerate(parts)]
    toks = reconstruct_words(ws, other_vocab=set(expected))
    assert keys(toks) == expected


def test_lexical_hyphen_kept_when_other_side_uses_it():
    ws = [W("non-", 200, 100, 0), W("payment", 10, 112, 1)]
    toks = reconstruct_words(ws, other_vocab={"non-payment"})
    assert keys(toks) == ["non-payment"]


def test_hyphen_inside_a_line_is_never_joined():
    ws = [W("non-", 10, 100, 0), W("payment", 40, 100, 0)]   # same line
    toks = reconstruct_words(ws, other_vocab={"nonpayment"})
    assert keys(toks) == ["non-", "payment"]


def test_hyphen_across_blocks_is_never_joined():
    ws = [W("Pre-", 10, 100, 0, block_id=1), W("mium", 10, 112, 1, block_id=2)]
    toks = reconstruct_words(ws, other_vocab={"premium"})
    assert keys(toks) == ["pre-", "mium"]


def test_numeric_or_identifier_hyphen_is_never_joined():
    ws = [W("116N198V0-", 10, 100, 0), W("8", 10, 112, 1)]
    toks = reconstruct_words(ws, other_vocab={"116n198v08"})
    assert keys(toks) == ["116n198v0-", "8"]
    ws = [W("0120-", 10, 100, 0), W("2514252", 10, 112, 1)]
    assert keys(reconstruct_words(ws, other_vocab={"01202514252"})) == ["0120-", "2514252"]


def test_unhyphenated_split_at_line_break_joined_only_with_evidence():
    ws = [W("Relationshi", 10, 100, 0), W("p", 10, 112, 1)]
    assert keys(reconstruct_words(ws, other_vocab={"relationship"})) == ["relationship"]
    ws = [W("Policy", 10, 100, 0), W("holder", 10, 112, 1)]
    assert keys(reconstruct_words(ws, other_vocab={"policyholder", "policy"})) == ["policyholder"]


@pytest.mark.parametrize("parts,other,expected", [
    (["Non-", "Forfeiture"], {"non-forfeiture"}, ["non-forfeiture"]),          # lexical hyphen, capital continuation
    (["(Post-", "Mortem"], {"post-mortem"}, ["post-mortem"]),
    (["Authority/", "IRDAI;"], {"authority/irdai"}, ["authority/irdai"]),     # slash at line end
    (["Email:bimalokpal.", "delhi@cioins.co.in"], {"email:bimalokpal.delhi@cioins.co.in"},
     ["email:bimalokpal.delhi@cioins.co.in"]),                                 # email broken after '.'
    (["bimalokpal.thane@", "cioins.co.in"], {"bimalokpal.thane@cioins.co.in"}, ["bimalokpal.thane@cioins.co.in"]),
    (["Email:bi-", "malokpal.guwahati@cioins.co.in"], {"email:bimalokpal.guwahati@cioins.co.in"},
     ["email:bimalokpal.guwahati@cioins.co.in"]),
    (["0612-", "2547068"], {"0612-2547068"}, ["0612-2547068"]),                # number: hyphen kept, exact evidence
    (["30-", "days"], {"30-days"}, ["30-days"]),
])
def test_line_break_join_with_exact_evidence(parts, other, expected):
    ws = [W(p, 10, 100 + 12 * i, i) for i, p in enumerate(parts)]
    assert keys(reconstruct_words(ws, other_vocab=other)) == expected


@pytest.mark.parametrize("parts", [["months.", "•"], [">)", "FOR"], ["“", "Medical"]])
def test_join_must_create_a_new_word_not_swallow_punctuation(parts):
    ws = [W(p, 10, 100 + 12 * i, i) for i, p in enumerate(parts)]
    toks = reconstruct_words(ws, other_vocab={"months", "for", "medical"})
    assert len(toks) == 2


def test_number_never_loses_its_hyphen_even_with_evidence():
    ws = [W("0612-", 10, 100, 0), W("2547068", 10, 112, 1)]
    assert keys(reconstruct_words(ws, other_vocab={"06122547068"})) == ["0612-", "2547068"]


def test_real_two_word_phrase_not_merged_even_if_other_side_concatenates():
    # 'may be' vs 'maybe' is a genuine wording change; both halves are words the other side uses
    ws = [W("may", 10, 100, 0), W("be", 10, 112, 1)]
    assert keys(reconstruct_words(ws, other_vocab={"maybe", "may", "be"})) == ["may", "be"]


def test_same_line_space_never_merged():
    ws = [W("Policy", 10, 100, 0), W("holder", 50, 100, 0)]
    assert keys(reconstruct_words(ws, other_vocab={"policyholder"})) == ["policy", "holder"]


def test_ocr_split_box_with_tiny_gap_joined_with_evidence():
    ws = [W("bimalok", 10, 100, 0, cw=5.0), W("pal", 10 + 35 + 0.4, 100, 0)]
    assert keys(reconstruct_words(ws, other_vocab={"bimalokpal"})) == ["bimalokpal"]


def test_vocabulary_contains_joined_and_plain_forms():
    ws = [W("Per-", 10, 100, 0), W("manent", 10, 112, 1), W("Total", 40, 112, 1)]
    v = vocabulary(build_tokens(ws))
    assert {"per-", "manent", "total", "permanent", "per-manent"} <= v


def test_build_tokens_preserves_one_token_per_word():
    ws = [W("Grace", 10, 100, 0), W("Period,", 40, 100, 0)]
    toks = build_tokens(ws)
    assert [(t.text, t.key, t.src) for t in toks] == [("Grace", "grace", (0,)), ("Period,", "period", (1,))]


def test_join_across_a_page_break():
    # a table row split over two pages: "... Non-" (last word, p1) / "Forfeiture" (first word, p2)
    ws = [W("and", 10, 780, 50, page=1), W("Non-", 40, 780, 50, page=1), W("Forfeiture", 10, 40, 51, block_id=9, page=2)]
    assert keys(reconstruct_words(ws, other_vocab={"non-forfeiture", "and"})) == ["and", "non-forfeiture"]
    ws = [W("Instal-", 40, 780, 50, page=3), W("ment", 10, 40, 51, block_id=9, page=4)]
    assert keys(reconstruct_words(ws, other_vocab={"instalment"})) == ["instalment"]


def test_dotted_leader_key_is_placeholder():
    from app.services.compare_tokens import PLACEHOLDER_KEY
    assert comparison_key("…………………") == PLACEHOLDER_KEY
    assert comparison_key("..........") == PLACEHOLDER_KEY
    assert comparison_key("etc.") != PLACEHOLDER_KEY


def test_word_broken_inside_a_narrow_cell_rejoins_even_if_halves_look_like_words():
    # LibreOffice breaks "Nominee(s)" as "Nominee(" / "s)" in a narrow table cell; a
    # one-letter fragment is not a word, so the 'may be' guard must not block the join
    ws = [W("Nominee(", 10, 100, 0), W("s)", 10, 112, 1)]
    assert keys(reconstruct_words(ws, other_vocab={"nominee(s", "nominee", "s"})) == ["nominee(s"]
