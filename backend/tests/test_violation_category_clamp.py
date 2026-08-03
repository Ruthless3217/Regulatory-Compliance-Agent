"""violations.category must never be able to abort a run.

`severity` is validated against an allow-list before insert; `category` is free
text the model picks per finding. On 2026-07-31 a label exceeded the column
width, psycopg2 raised StringDataRightTruncation on the batch INSERT, and
because all violations are inserted in one statement the failure discarded ALL
108 findings of a completed analysis — after the entire LLM spend.

These pin the write-side clamp. Losing a label is acceptable; losing a run is
not.
"""
from app.services.agents.compliance.engine import _CATEGORY_MAX_LEN


def _clamp(raw):
    """The exact normalisation persist_results applies to `category`."""
    cat = str(raw if raw is not None else "unknown").strip().lower() or "unknown"
    if len(cat) > _CATEGORY_MAX_LEN:
        cat = cat[:_CATEGORY_MAX_LEN].rstrip()
    return cat


def test_clamp_width_matches_the_column():
    # If someone narrows the column without narrowing this, the crash returns.
    from app.models.violation import Violation

    assert Violation.__table__.c.category.type.length == _CATEGORY_MAX_LEN


def test_over_long_category_is_truncated_not_raised():
    raw = "x" * 500
    out = _clamp(raw)
    assert len(out) <= _CATEGORY_MAX_LEN
    assert out  # never empty


def test_the_real_production_label_survives_intact():
    # The longest label actually observed in the deployed dashboard. It fit in
    # 50 only by luck; it must survive un-truncated now.
    raw = "Claim settlement ratio disclosure & approval"
    assert _clamp(raw) == raw.lower()


def test_realistic_long_llm_label_survives():
    raw = "Premium payment timing and risk commencement clarification required"
    assert _clamp(raw) == raw.lower()
    assert len(raw) <= _CATEGORY_MAX_LEN


def test_ordinary_categories_unchanged():
    for raw in ("brand", "regulatory", "mandatory disclosure", "product compliance"):
        assert _clamp(raw) == raw


def test_missing_or_blank_category_falls_back():
    assert _clamp(None) == "unknown"
    assert _clamp("") == "unknown"
    assert _clamp("   ") == "unknown"


def test_case_and_whitespace_normalised():
    assert _clamp("  BRAND  ") == "brand"


def test_truncation_does_not_leave_trailing_space():
    raw = ("word " * 100)  # spaces land near the cut
    out = _clamp(raw)
    assert out == out.rstrip()
