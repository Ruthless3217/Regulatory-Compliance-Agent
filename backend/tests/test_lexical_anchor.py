"""Lexical anchoring: node-relative offsets plus a text fingerprint.

Character offsets into the extracted text drift the moment a reviewer edits in
the Lexical editor. A violation therefore also records the node it lives in,
offsets *within* that node, and a fingerprint of its surroundings so the span
can be relocated after Lexical re-keys nodes.

The fingerprint must be stable across processes, which is why it is a hashlib
digest and not Python's `hash()` (salted per interpreter by PYTHONHASHSEED —
writer and reader would disagree). The literal digests below are the contract:
sha256 over whitespace-collapsed, lowercased text, truncated to 32 hex chars.
"""
import uuid

from sqlalchemy import Integer, String

from app.models.violation import Violation
from app.services.lexical_anchor import compute_anchor_fingerprint, fingerprint
from app.services.violation_serializer import serialize_violation

ANCHOR_COLUMNS = {
    "anchor_node_key": (String, 64),
    "anchor_offset_start": (Integer, None),
    "anchor_offset_end": (Integer, None),
    "anchor_fingerprint": (String, 128),
}


def _violation(**overrides) -> Violation:
    v = Violation(
        id=uuid.uuid4(),
        category="disclosure",
        severity="high",
        description="desc",
    )
    for k, val in overrides.items():
        setattr(v, k, val)
    return v


def test_anchor_columns_exist_with_declared_types():
    cols = Violation.__table__.columns
    for name, (expected_type, length) in ANCHOR_COLUMNS.items():
        assert name in cols, f"violations.{name} is missing"
        col_type = cols[name].type
        assert isinstance(col_type, expected_type), f"violations.{name} is {col_type!r}"
        if length is not None:
            assert col_type.length == length


def test_all_four_anchor_columns_are_nullable():
    # Every existing violation has none of these; the text-offset path must
    # keep working untouched.
    cols = Violation.__table__.columns
    for name in ANCHOR_COLUMNS:
        assert cols[name].nullable is True, f"violations.{name} must be nullable"

    v = _violation()
    for name in ANCHOR_COLUMNS:
        assert getattr(v, name) is None


def test_fingerprint_is_stable_across_calls_and_processes():
    # Literal digest, not self-equality: a per-process salted hash() would pass
    # a self-equality assertion and still break writer/reader agreement.
    assert fingerprint("hello world") == "b94d27b9934d3e08a52e52d7da7dabfa"
    assert fingerprint("hello world") == fingerprint("hello world")


def test_fingerprint_normalizes_whitespace_and_case():
    assert fingerprint("  Hello   WORLD\n") == fingerprint("hello world")
    assert fingerprint("hello\t\nworld") == fingerprint("hello world")
    assert fingerprint("HELLO WORLD") == fingerprint("hello world")


def test_fingerprint_still_distinguishes_different_text():
    assert fingerprint("hello world") != fingerprint("hello worlds")


def test_fingerprint_is_short_hex():
    fp = fingerprint("The free-look period is 15 days.")
    assert fp == "96f032ad011b16b2b92c707549bf24f8"
    assert len(fp) == 32
    assert all(c in "0123456789abcdef" for c in fp)


def test_compute_anchor_fingerprint_hashes_the_concatenation():
    # Surroundings are part of the identity, so an edit to the span alone can
    # still be relocated by what sits either side of it.
    assert compute_anchor_fingerprint("before ", "SPAN", " after") == fingerprint(
        "before SPAN after"
    )
    assert compute_anchor_fingerprint("before ", "SPAN", " after") == (
        "8f992f714f2d029ff0611c0a327bf99a"
    )


def test_compute_anchor_fingerprint_shifts_when_surroundings_change():
    span = "guaranteed returns"
    assert compute_anchor_fingerprint("we offer ", span, " to you") != (
        compute_anchor_fingerprint("we never offer ", span, " to you")
    )


def test_serializer_emits_the_anchor_fields():
    v = _violation(
        anchor_node_key="42",
        anchor_offset_start=7,
        anchor_offset_end=25,
        anchor_fingerprint=fingerprint("guaranteed returns"),
    )
    out = serialize_violation(v)
    assert out["anchor_node_key"] == "42"
    assert out["anchor_offset_start"] == 7
    assert out["anchor_offset_end"] == 25
    assert out["anchor_fingerprint"] == fingerprint("guaranteed returns")


def test_serializer_emits_nulls_for_unanchored_violations():
    out = serialize_violation(_violation())
    for name in ANCHOR_COLUMNS:
        assert out[name] is None
