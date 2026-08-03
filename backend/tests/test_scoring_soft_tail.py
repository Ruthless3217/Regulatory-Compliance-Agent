"""Focused contract tests for absolute soft-tail compliance scoring."""

import pytest

from app.services.agents.compliance.scoring import ScoringService


def _findings(count, *, severity="moderate", category="regulatory", **extra):
    return [
        {"severity": severity, "category": category, **extra}
        for _ in range(count)
    ]


def test_soft_tail_is_continuous_at_the_deduction_knee():
    score = ScoringService._score_from_deduction
    epsilon = 1e-6

    assert score(50.0) == 50.0
    assert score(50.0 - epsilon) == pytest.approx(50.0 + epsilon)
    assert score(50.0 + epsilon) == pytest.approx(50.0, abs=epsilon)


@pytest.mark.parametrize(
    ("burden", "expected_score", "expected_grade"),
    [
        (0.0, 100.0, "A"),
        (10.0, 90.0, "A"),
        (20.0, 80.0, "B"),
        (30.0, 70.0, "C"),
        (40.0, 60.0, "D"),
        (50.0, 50.0, "F"),
    ],
)
def test_linear_region_and_existing_grade_thresholds_are_preserved(
    burden, expected_score, expected_grade
):
    score = ScoringService._score_from_deduction(burden)

    assert score == expected_score
    assert ScoringService._get_grade(score) == expected_grade


def test_score_is_strictly_monotonic_across_linear_and_tail_regions():
    burdens = [0.0, 10.0, 49.0, 50.0, 51.0, 100.0, 500.0, 10_000.0]
    scores = [ScoringService._score_from_deduction(value) for value in burdens]

    assert all(left > right for left, right in zip(scores, scores[1:]))
    assert all(value > 0.0 for value in scores)


def test_high_burdens_remain_nonzero_distinguishable_and_failed():
    fifteen = ScoringService.calculate_scores(_findings(15))
    one_hundred_nine = ScoringService.calculate_scores(_findings(109))

    assert 0.0 < one_hundred_nine["overall"] < fifteen["overall"]
    assert fifteen["grade"] == one_hundred_nine["grade"] == "F"
    assert fifteen["status"] == one_hundred_nine["status"] == "failed"


def test_category_and_overall_use_the_same_soft_tail_curve():
    result = ScoringService.calculate_scores(_findings(15, category="disclosure"))
    expected = ScoringService._score_from_deduction(15 * 8)

    assert result["overall"] == pytest.approx(expected, abs=0.01)
    assert result["disclosure"] == pytest.approx(expected, abs=0.01)


def test_pipe_separated_category_tokens_are_trimmed_for_scoring():
    result = ScoringService.calculate_scores(
        _findings(1, severity="medium", category=" brand | disclosure ")
        + _findings(1, severity="low", category="brand| legal")
    )

    assert result["brand"] == 93.0
    assert result["disclosure"] == 95.0
    assert result["legal"] == 98.0
    assert not any(key != key.strip() for key in result)


def test_high_confidence_critical_still_caps_the_overall_score():
    result = ScoringService.calculate_scores(
        _findings(1, severity="critical", confidence=0.75)
    )

    assert result["weighted_burden"] == 15.0
    assert result["overall"] == ScoringService.CRITICAL_SCORE_CAP
    assert result["grade"] == "C"
    assert result["status"] == "failed"


def test_suppressed_findings_are_excluded_from_scores_categories_and_cap():
    result = ScoringService.calculate_scores(
        _findings(1, severity="low", category="brand")
        + _findings(
            1,
            severity="critical",
            category="suppressed-only",
            confidence=1.0,
            suppressed=True,
        )
    )

    assert result["weighted_burden"] == 2.0
    assert result["overall"] == 98.0
    assert result["brand"] == 98.0
    assert "suppressed-only" not in result
    assert result["grade"] == "A"
    assert result["status"] == "passed"


def test_result_exposes_scoring_policy_metrics():
    result = ScoringService.calculate_scores(
        _findings(2, severity="medium", confidence=0.5)
        + _findings(3, severity="critical", suppressed=True)
    )

    assert result["weighted_burden"] == 5.0
    assert result["scored_finding_count"] == 2
    assert result["suppressed_finding_count"] == 3
    assert result["scoring_policy_version"] == "absolute-soft-tail-v2"
