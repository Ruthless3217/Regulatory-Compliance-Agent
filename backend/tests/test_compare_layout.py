"""Stage A — source normalisation & reading-order repair (synthetic, no real PDF).

Reading order must follow *content*, not raw y-sorting across the full page width:
two-column text is read column by column, table cells are read cell by cell (row-major),
and sub-point baseline jitter must not reorder words on one visual line.
"""
from app.services.pdf_render_service import PositionedWord
from app.services.compare_layout import order_words
from app.services.comparison_service import _detect_running_lines


def W(text, x0, y0, page=1, h=10.0, cw=5.0):
    return PositionedWord(text=text, page=page, x0=x0, y0=y0, x1=x0 + cw * len(text), y1=y0 + h)


def line(words_text, x0, y0, page=1, gap=3.0, cw=5.0):
    out, x = [], x0
    for t in words_text.split():
        out.append(W(t, x, y0, page, cw=cw))
        x += cw * len(t) + gap
    return out


def texts(ws):
    return " ".join(w.text for w in ws)


# ---------------------------------------------------------------- columns
def test_two_columns_under_full_width_header_read_column_by_column():
    header = line("Bajaj Life eTouch II policy document heading spanning the whole width", 40, 20)
    left = line("left one a", 40, 60) + line("left two b", 40, 75) + line("left three c", 40, 90) + line("left four d", 40, 105)
    right = line("right one a", 320, 60) + line("right two b", 320, 75) + line("right three c", 320, 90) + line("right four d", 320, 105)
    shuffled = sorted(header + left + right, key=lambda w: (w.y0, w.x0))  # today's interleaved order
    ordered = order_words(shuffled, page_sizes={1: (595.0, 842.0)})
    assert texts(ordered) == texts(header) + " " + texts(left) + " " + texts(right)


def test_narrow_gutter_with_right_column_clause_numbers_is_found():
    # eTouch PDF p9: justified left column ends at x≈292, right column numbers start at
    # x≈303.6 ("11.") and text at 330 — an 11.6pt gutter with ~4.8pt word gaps
    left, right = [], []
    for k, y in enumerate(range(100, 100 + 13 * 8, 13)):
        # 35 chars × 6pt + 7 gaps × 4.8pt = 243.6pt, starting at 48.4 -> ends at x = 292
        left += line("the claim shall be paid within thirty days", 48.4, y, gap=4.8, cw=6.0)
        if k == 2:
            right.append(W("11.", 303.6, y, cw=4.5))
        right += line("Termination of the policy", 330, y, gap=4.8, cw=5.0)
    ordered = order_words(sorted(left + right, key=lambda w: (w.y0, w.x0)), page_sizes={1: (595.0, 842.0)})
    assert texts(ordered) == texts(sorted(left, key=lambda w: (w.y0, w.x0))) + " " + texts(sorted(right, key=lambda w: (w.y0, w.x0)))


def test_single_column_numbered_list_is_not_split_into_columns():
    ws = []
    for i, y in enumerate((60, 75, 90, 105)):
        ws.append(W(f"({i + 1})", 40, y))
        ws += line("You shall be provided a free look period of thirty days", 62, y)
    ordered = order_words(sorted(ws, key=lambda w: (w.x0, w.y0)), page_sizes={1: (595.0, 842.0)})
    expected = []
    for i, y in enumerate((60, 75, 90, 105)):
        expected.append(f"({i + 1})")
        expected.append("You shall be provided a free look period of thirty days")
    assert texts(ordered) == " ".join(expected)


def test_baseline_jitter_keeps_visual_line_order():
    a = [W("alpha", 40, 100.4), W("beta", 80, 100.0), W("gamma", 120, 100.3)]
    ordered = order_words(list(reversed(a)), page_sizes={1: (595.0, 842.0)})
    assert texts(ordered) == "alpha beta gamma"


# ---------------------------------------------------------------- tables
def test_table_cells_read_row_major_cell_by_cell():
    # header row: two cells, each wrapping onto two lines with slightly different baselines
    c1 = line("Name of the", 40, 100) + line("Rider", 40, 112)
    c2 = line("Date of Com-", 160, 100.6) + line("mencement", 160, 112.4)
    c3 = line("Rider Term", 280, 106)
    r2a = line("Family Protect Rider", 40, 140)
    r2b = line("NA", 160, 140)
    r2c = line("NA", 280, 140)
    cells = {1: [(36, 96, 150, 126), (150, 96, 270, 126), (270, 96, 400, 126),
                 (36, 136, 150, 156), (150, 136, 270, 156), (270, 136, 400, 156)]}
    shuffled = sorted(c1 + c2 + c3 + r2a + r2b + r2c, key=lambda w: (round(w.y0), w.x0))
    ordered = order_words(shuffled, page_sizes={1: (595.0, 842.0)}, table_cells=cells)
    assert texts(ordered) == "Name of the Rider Date of Com- mencement Rider Term Family Protect Rider NA NA"


def test_block_and_line_ids_assigned_per_cell():
    c1 = line("Critical Ill-", 40, 100) + line("ness Benefit", 40, 112)
    c2 = line("NA", 160, 100)
    cells = {1: [(36, 96, 150, 126), (150, 96, 270, 126)]}
    ordered = order_words(c2 + c1, page_sizes={1: (595.0, 842.0)}, table_cells=cells)
    ill, ness, na = (next(w for w in ordered if w.text == t) for t in ("Ill-", "ness", "NA"))
    assert ill.block_id == ness.block_id != na.block_id
    assert ness.line_id == ill.line_id + 1


def test_pages_stay_in_order():
    p1 = line("first page text", 40, 100, page=1)
    p2 = line("second page text", 40, 50, page=2)
    ordered = order_words(p2 + p1, page_sizes={1: (595.0, 842.0), 2: (595.0, 842.0)})
    assert texts(ordered) == "first page text second page text"


# ---------------------------------------------------------------- page furniture
def test_phone_numbers_are_not_page_numbers():
    from app.services.comparison_service import is_page_number_line
    assert not is_page_number_line("26652048 / 26652049")      # Bengaluru ombudsman phones
    assert is_page_number_line("3 / 22")
    assert is_page_number_line("Page 4 of 26")
    assert is_page_number_line("12")
    assert not is_page_number_line("22 / 3")                    # not a counter


def test_furniture_is_only_stripped_in_the_top_and_bottom_band():
    from app.services.comparison_service import strip_page_furniture
    running = {"Bajaj Life eTouch II", "Policy Document Ver. 1 (062026)"}
    lines = ["Bajaj Life eTouch II", "A Non-Linked plan", "UIN: 116N198V08",
             "Bajaj Life Insurance Limited", "Bajaj Life eTouch II",        # body title, same text as header
             "Tel.: 080 -", "26652048 / 26652049", "Email: x@y",            # a phone number alone on a line
             "body", "Policy Document Ver. 1 (062026)", "7"]
    assert strip_page_furniture(lines, running) == lines[1:-2]


# ---------------------------------------------------------------- running lines
def test_running_footer_with_changing_page_numbers_and_file_name_is_detected():
    pages = []
    for n in range(1, 8):
        pages.append([
            "Bajaj Life eTouch II",
            f"Body content unique to page {n} with clause {n * 3}",
            "more body",
            f"Bajaj Life Insurance Limited a24605ae-6377-4573-8911-a630c07f48ec.docx Page {n} of 26",
        ])
    running = _detect_running_lines(pages)
    assert "Bajaj Life Insurance Limited a24605ae-6377-4573-8911-a630c07f48ec.docx Page 5 of 26" in running
    assert "Bajaj Life eTouch II" in running
    assert not any(l.startswith("Body content") for l in running)


def test_wrapped_footer_counter_is_detected():
    # LibreOffice wraps "... Page 7 of" / "26" when the footer line is full
    pages = [["head", f"body {n}", "body", f"Bajaj Life Insurance Limited bf02cee2.docx Page {n} of", "26"] for n in range(1, 9)]
    running = _detect_running_lines(pages)
    assert "Bajaj Life Insurance Limited bf02cee2.docx Page 4 of" in running


def test_footer_wrapping_differently_on_one_and_two_digit_pages_is_detected():
    # pages 1-9 wrap as "... Page 7 of" / "26"; pages 10-26 as "... Page 17" / "of 26"
    pages = []
    for n in range(1, 27):
        foot = [f"Limited x.docx Page {n} of", "26"] if n < 10 else [f"Limited x.docx Page {n}", "of 26"]
        pages.append(["head", f"body {n}", "body"] + foot)
    running = _detect_running_lines(pages)
    assert "Limited x.docx Page 3 of" in running and "Limited x.docx Page 17" in running
    assert "of 26" in running


def test_body_sentence_mentioning_a_page_is_kept():
    pages = [[f"See the schedule on page {n} for details", "x", "y", "z"] for n in range(1, 9)]
    assert not any(l.startswith("See the schedule") for l in _detect_running_lines(pages))


def test_numbered_clause_lines_are_not_mistaken_for_running_lines():
    # numbers vary per page but there is no page-counter evidence -> must stay content
    pages = [[f"Clause {n * 7} text differs entirely number {n}", "x", "y", f"Page {n} of 9"] for n in range(1, 10)]
    running = _detect_running_lines(pages)
    assert not any(l.startswith("Clause") for l in running)


def test_word_spanning_two_table_cells_is_split_at_the_border():
    from app.services.pdf_render_service import _split_word_by_cells
    chars = [{"text": c, "x0": 100 + 5 * i, "x1": 105 + 5 * i, "top": 50, "bottom": 60, "fontname": "F"}
             for i, c in enumerate("DELHIAsaf")]
    word = {"text": "DELHIAsaf", "x0": 100, "x1": 145, "top": 50, "bottom": 60, "chars": chars}
    cells = [(90, 40, 124.5, 70), (124.5, 40, 300, 70)]
    parts = _split_word_by_cells(word, cells)
    assert [p["text"] for p in parts] == ["DELHI", "Asaf"]
    assert parts[0]["x1"] == 125 and parts[1]["x0"] == 125
    assert _split_word_by_cells(word, []) == [word]
