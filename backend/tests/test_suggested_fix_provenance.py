"""`suggested_fix` must be a rewrite of THIS passage, or absent.

A precedent's `final_text_chunk` is a fragment of the corrected version of a
different historical document. It was being offered as this finding's suggested
fix, so "Apply fix" spliced unrelated text over the reviewer's sentence —
observed live replacing "Multiple funds to choose from" with "at".

The precedent's wording is still surfaced as provenance through
`cited_final_text`. These tests pin the separation: provenance is shown,
never proposed as an edit.
"""
import inspect
import re

from app.services.agents.graph import nodes


def _assignments(field: str) -> list[str]:
    """Every right-hand side assigned to `field` in nodes.py."""
    src = inspect.getsource(nodes)
    return [m.group(1).strip() for m in re.finditer(rf'"{field}":\s*([^\n]+?),\s*\n', src)]


def test_no_finding_takes_its_suggested_fix_from_a_precedent():
    for rhs in _assignments("suggested_fix"):
        assert "final_text_chunk" not in rhs, (
            "a precedent's final text is another document's correction, not a "
            f"rewrite of this passage: {rhs}"
        )


def test_precedent_final_text_is_still_kept_as_provenance():
    """Removing it from suggested_fix must not remove it from the citation."""
    assert any("final_text_chunk" in rhs for rhs in _assignments("cited_final_text")), (
        "cited_final_text should still carry the precedent's corrected wording"
    )


def test_only_the_disclosure_path_proposes_wording():
    """Disclosure findings carry approved registry wording, which IS the fix.

    Everything else must be None until something generates a rewrite for the
    actual passage — the on-demand rewrite endpoint.
    """
    non_null = {rhs for rhs in _assignments("suggested_fix") if rhs != "None"}
    assert non_null == {
        "d.text",
        # The reuse path (_prior_violation_to_state) mirrors an ALREADY
        # PERSISTED finding back into graph state when its chunk is unchanged.
        # It copies the stored value; it does not choose one, so it cannot
        # introduce a wrong source — whatever it carries was vetted by this
        # same guard when the finding was first created.
        "v.suggested_fix",
    }, f"unexpected suggested_fix source(s): {non_null}"
