import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from app.services.agents.validators import validate_agent_output


def _valid():
    return {
        "category": "legal language",
        "severity": "critical",
        "description": "This makes an unsupported guarantee claim.",
        "confidence": 0.9,
    }


def test_valid_output_passes():
    ok, errs = validate_agent_output(_valid())
    assert ok is True
    assert errs == []


def test_non_dict_fails():
    ok, errs = validate_agent_output("nope")
    assert ok is False
    assert errs


def test_empty_category_fails():
    v = _valid(); v["category"] = ""
    ok, errs = validate_agent_output(v)
    assert ok is False
    assert any("category" in e for e in errs)


def test_bad_severity_fails():
    v = _valid(); v["severity"] = "high"  # not in new vocab
    ok, errs = validate_agent_output(v)
    assert ok is False
    assert any("severity" in e for e in errs)


def test_short_description_fails():
    v = _valid(); v["description"] = "too short"
    ok, errs = validate_agent_output(v)
    assert ok is False
    assert any("description" in e for e in errs)


def test_confidence_out_of_range_fails():
    v = _valid(); v["confidence"] = 1.5
    ok, errs = validate_agent_output(v)
    assert ok is False
    assert any("confidence" in e for e in errs)


def test_violation_found_must_be_bool_if_present():
    v = _valid(); v["violation_found"] = "yes"
    ok, errs = validate_agent_output(v)
    assert ok is False
    assert any("violation_found" in e for e in errs)


def test_score_impact_in_range_if_present():
    v = _valid(); v["score_impact"] = 2.0
    ok, errs = validate_agent_output(v)
    assert ok is False
    assert any("score_impact" in e for e in errs)


def test_boolean_confidence_fails():
    v = _valid(); v["confidence"] = True
    ok, errs = validate_agent_output(v)
    assert ok is False
    assert any("confidence" in e for e in errs)
