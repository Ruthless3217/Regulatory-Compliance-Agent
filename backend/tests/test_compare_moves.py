"""Move detection in the diff engine (text blocks + pixel changes)."""
from app.services.comparison_service import (
    build_diff,
    detect_moves_in_blocks,
    word_level_ops,
    _MOVE_MIN_TOKENS,
)


def _texts(*sentences):
    return list(sentences)


def test_no_moves_leaves_blocks_untouched():
    blocks = build_diff(["The sky is blue today."], ["The sky is blue today."])
    assert all(not b.get("moved") for b in blocks)
    assert [b["type"] for b in blocks] == ["equal"]


def test_moved_paragraph_tagged_on_both_ends():
    # A long run present in both, relocated: appears as delete (old pos) + insert (new pos).
    moved = "Policyholders may surrender the policy after five completed years without penalty."
    old = [moved, "Intro clause remains stable across both versions here."]
    new = ["Intro clause remains stable across both versions here.", moved]
    blocks = build_diff(old, new)
    moved_blocks = [b for b in blocks if b.get("moved")]
    assert len(moved_blocks) == 2, [b.get("type") for b in blocks]
    ids = {b["move_id"] for b in moved_blocks}
    assert ids == {"m0"}
    kinds = sorted(b["type"] for b in moved_blocks)
    assert kinds == ["delete", "insert"]


def test_short_run_below_threshold_is_not_a_move():
    # Under _MOVE_MIN_TOKENS tokens => stays a plain delete/insert, not a move.
    assert _MOVE_MIN_TOKENS == 5
    old = ["Two words.", "A stable shared sentence that anchors the alignment across versions."]
    new = ["A stable shared sentence that anchors the alignment across versions.", "Two words."]
    blocks = build_diff(old, new)
    assert all(not b.get("moved") for b in blocks)


def test_detect_moves_in_blocks_is_direct_and_idempotentish():
    blocks = [
        {"type": "delete", "old_text": "one two three four five six seven"},
        {"type": "insert", "new_text": "one two three four five six seven"},
    ]
    out = detect_moves_in_blocks(blocks)
    assert out[0]["moved"] and out[1]["moved"]
    assert out[0]["move_id"] == out[1]["move_id"] == "m0"


def test_word_level_ops_merges_moved_pair_into_single_change():
    moved = ["Surrender", "is", "permitted", "after", "five", "completed", "policy", "years."]
    old_texts = moved + ["Stable", "anchor", "clause", "shared", "by", "both", "documents", "here."]
    new_texts = ["Stable", "anchor", "clause", "shared", "by", "both", "documents", "here."] + moved
    old_marks, new_marks, changes = word_level_ops(old_texts, new_texts)
    moved_changes = [c for c in changes if c["kind"] == "moved"]
    assert len(moved_changes) == 1, [c["kind"] for c in changes]
    mc = moved_changes[0]
    # A move means identical text on both sides (difflib anchors one of the two
    # equal-length runs and reports the other as the move — either is correct).
    assert mc["old_text"] == mc["new_text"]
    assert len(mc["old_text"].split()) >= _MOVE_MIN_TOKENS
    assert any(m["change_id"] == mc["id"] for m in old_marks)
    assert any(m["change_id"] == mc["id"] for m in new_marks)
    # no orphaned added change left pointing at a dropped id
    change_ids = {c["id"] for c in changes}
    assert all(m["change_id"] in change_ids for m in new_marks)
