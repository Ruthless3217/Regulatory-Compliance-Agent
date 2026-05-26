import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.eval_precedent_replay import (
    split_by_hash,
    precision_recall,
    count_missed_criticals,
)


def test_split_is_deterministic_and_disjoint():
    files = [f"f{i}.json" for i in range(100)]
    train1, ev1 = split_by_hash(files, eval_frac=0.1)
    train2, ev2 = split_by_hash(files, eval_frac=0.1)
    assert train1 == train2 and ev1 == ev2          # deterministic
    assert set(train1).isdisjoint(set(ev1))          # disjoint
    assert len(train1) + len(ev1) == 100
    assert 5 <= len(ev1) <= 20                        # ~10%


def test_precision_recall_basic():
    # predicted flags on anchors {a,b}; real on {b,c}. TP=1 (b), FP=1 (a), FN=1 (c)
    p, r = precision_recall(predicted={"a", "b"}, actual={"b", "c"})
    assert round(p, 3) == 0.5
    assert round(r, 3) == 0.5


def test_precision_recall_empty_predicted():
    p, r = precision_recall(predicted=set(), actual={"b"})
    assert p == 0.0
    assert r == 0.0


def test_precision_recall_empty_actual():
    p, r = precision_recall(predicted={"a"}, actual=set())
    assert p == 0.0
    assert r == 1.0   # nothing to recall → recall defined as 1.0


def test_missed_criticals():
    real = [{"anchor": "x", "severity": "critical"}, {"anchor": "y", "severity": "moderate"}]
    matched_anchors = {"y"}
    assert count_missed_criticals(real, matched_anchors) == 1
