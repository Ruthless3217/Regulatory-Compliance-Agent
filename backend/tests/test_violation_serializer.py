"""serialize_violation: the single source of truth for violation JSON shape,
replacing compliance.py's `_serialize_violation` and engine.py's hand-rolled
`get_check_summary` dict — both of which silently dropped already-populated
columns (cited_section/cited_page/cited_regulation_version/rule_version).
"""
import uuid
from datetime import datetime, timezone

from app.models.violation import Violation
from app.models.rule_feedback import RuleFeedback
from app.services.violation_serializer import (
    finding_counts,
    latest_feedback_map,
    serialize_violation,
)


def _violation(**overrides) -> Violation:
    v = Violation(
        id=uuid.uuid4(),
        category="disclosure",
        severity="high",
        description="desc",
        confidence=0.9,
        suppressed=False,
        fix_applied=False,
    )
    for k, val in overrides.items():
        setattr(v, k, val)
    return v


def test_includes_previously_dropped_citation_fields():
    v = _violation(
        cited_section="Section 41",
        cited_page=12,
        cited_regulation_version="v3",
        rule_version=2,
    )
    out = serialize_violation(v)
    assert out["cited_section"] == "Section 41"
    assert out["cited_page"] == 12
    assert out["cited_regulation_version"] == "v3"
    assert out["rule_version"] == 2


def test_includes_reviewer_lifecycle_and_document_anchors():
    now = datetime.now(timezone.utc)
    v = _violation(
        review_status="actioned",
        resolved_at=now,
        analysis_run_id=uuid.uuid4(),
        section_title="Free-look period",
        anchor_page=3,
        anchor_bbox=[0.1, 0.2, 0.3, 0.4],
    )
    out = serialize_violation(v)
    assert out["review_status"] == "actioned"
    assert out["resolved_at"] == now.isoformat()
    assert out["analysis_run_id"] == str(v.analysis_run_id)
    assert out["section_title"] == "Free-look period"
    assert out["anchor_page"] == 3
    assert out["anchor_bbox"] == [0.1, 0.2, 0.3, 0.4]


def test_without_feedback_reviewer_fields_are_null():
    v = _violation()
    out = serialize_violation(v)
    assert out["reviewer_verdict"] is None
    assert out["reviewer_comment"] is None


def test_left_joins_the_supplied_feedback_row():
    v = _violation()
    fb = RuleFeedback(violation_id=v.id, verdict="correct", comment="looks right")
    out = serialize_violation(v, feedback=fb)
    assert out["reviewer_verdict"] == "correct"
    assert out["reviewer_comment"] == "looks right"


def test_uuid_and_bool_fields_are_json_safe():
    v = _violation(cited_precedent_id=uuid.uuid4(), rule_id=uuid.uuid4())
    out = serialize_violation(v)
    assert out["cited_precedent_id"] == str(v.cited_precedent_id)
    assert out["rule_id"] == str(v.rule_id)
    assert out["suppressed"] is False
    assert out["fix_applied"] is False


def test_authorship_defaults_to_model_when_unset():
    # A row read back before 0031's server_default is applied (or any legacy
    # in-memory object) must never look reviewer-authored.
    out = serialize_violation(_violation(source=None))
    assert out["source"] == "model"
    assert out["created_by"] is None
    assert out["created_by_username"] is None


def test_reviewer_authored_fields_round_trip():
    class _Creator:
        username = "asha.menon"

    author_id = uuid.uuid4()
    v = _violation(source="reviewer", created_by=author_id)
    v.creator = _Creator()
    out = serialize_violation(v)
    assert out["source"] == "reviewer"
    assert out["created_by"] == str(author_id)
    assert out["created_by_username"] == "asha.menon"


def test_latest_feedback_map_picks_most_recently_updated_per_violation():
    vid1, vid2 = uuid.uuid4(), uuid.uuid4()
    older = RuleFeedback(violation_id=vid1, verdict="not_violation",
                          updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    newer = RuleFeedback(violation_id=vid1, verdict="correct",
                          updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
    other = RuleFeedback(violation_id=vid2, verdict="dismiss",
                          updated_at=datetime(2026, 3, 1, tzinfo=timezone.utc))

    class _FakeQuery:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *a, **k):
            return self

        def order_by(self, *a, **k):
            return self

        def all(self):
            return self._rows

    class _FakeDb:
        def query(self, model):
            # order_by(updated_at.desc()) already applied server-side.
            return _FakeQuery(sorted([newer, older, other], key=lambda r: r.updated_at, reverse=True))

    out = latest_feedback_map(_FakeDb(), [vid1, vid2])
    assert out[str(vid1)] is newer
    assert out[str(vid2)] is other


def test_latest_feedback_map_empty_ids_returns_empty_without_querying():
    class _ExplodingDb:
        def query(self, model):
            raise AssertionError("should not query the DB when there are no ids")

    assert latest_feedback_map(_ExplodingDb(), []) == {}


def test_finding_counts_separates_model_review_and_reviewer_authorship():
    rows = [
        _violation(suppressed=False, source="model"),
        _violation(suppressed=False, source=None),
        _violation(suppressed=True, source="model"),
        _violation(suppressed=False, source="reviewer"),
    ]

    assert finding_counts(rows) == {
        "scored": 2,
        "suppressed": 1,
        "reviewer_added": 1,
        "total": 4,
    }


def test_finding_counts_accepts_serialized_dicts_too():
    rows = [
        {"suppressed": False},
        {"suppressed": True},
        {},
        {"source": "reviewer", "suppressed": False},
    ]

    assert finding_counts(rows) == {
        "scored": 2,
        "suppressed": 1,
        "reviewer_added": 1,
        "total": 4,
    }
