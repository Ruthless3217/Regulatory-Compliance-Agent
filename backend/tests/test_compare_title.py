"""Deriving a comparison's title from its two sides.

The title is the only thing that distinguishes rows in the Compare list, so a
constant placeholder made twenty comparisons indistinguishable. These pin the
cases that made the derivation worth having: two similar file names must stay
tellable apart, and a pasted body — the form's default tab — must not fall back
to something generic.
"""
from app.api.routes.comparisons import _derive_title


def test_file_names_lose_only_their_extension():
    assert _derive_title("old.docx", "new.docx", None, None) == "old → new"


def test_near_identical_names_stay_distinguishable():
    """The corpus is full of these; truncating the tail would merge them."""
    title = _derive_title(
        "IRDAI-Annexure-Health-Indemnity-2024-rev-A.pdf",
        "IRDAI-Annexure-Health-Indemnity-2024-rev-B.pdf",
        None,
        None,
    )
    assert title == (
        "IRDAI-Annexure-Health-Indemnity-2024-rev-A → "
        "IRDAI-Annexure-Health-Indemnity-2024-rev-B"
    )


def test_only_the_final_extension_is_dropped():
    assert _derive_title("archive.tar.gz", "README", None, None) == "archive.tar → README"


def test_pasted_text_is_named_after_its_first_real_line():
    title = _derive_title(None, None, "\n\n  Smart Protect Goal v1  \nbody", "Smart Protect Goal v2")
    assert title == "Smart Protect Goal v1 → Smart Protect Goal v2"


def test_a_long_paste_is_capped_but_a_file_name_is_not():
    long_line = "x" * 200
    assert _derive_title(None, None, long_line, long_line) == f"{'x' * 60} → {'x' * 60}"


def test_empty_sides_fall_back_to_side_labels():
    assert _derive_title(None, None, "   ", None) == "Original → Revised"


def test_a_file_and_a_paste_mix_cleanly():
    assert _derive_title("brochure.docx", None, None, "Revised wording") == (
        "brochure → Revised wording"
    )
