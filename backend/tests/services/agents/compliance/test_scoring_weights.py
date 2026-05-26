import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))

from app.services.agents.compliance.scoring import ScoringService


def test_existing_weights_unchanged():
    w = ScoringService.SEVERITY_WEIGHTS
    assert w["critical"] == 20
    assert w["high"] == 10
    assert w["medium"] == 5
    assert w["low"] == 2


def test_new_vocab_weights_added():
    w = ScoringService.SEVERITY_WEIGHTS
    assert w["moderate"] == 8
    assert w["informational"] == 2


def test_critical_still_triggers_failed_status():
    # _get_status fails on any critical violation.
    status = ScoringService._get_status([{"severity": "critical"}], overall_score=95.0)
    assert status == "failed"


def test_informational_does_not_force_fail():
    status = ScoringService._get_status([{"severity": "informational"}], overall_score=95.0)
    assert status == "passed"
