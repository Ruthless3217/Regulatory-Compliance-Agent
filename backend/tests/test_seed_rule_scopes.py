"""Seeded active rules must declare a supported product applicability scope."""

from pathlib import Path
import uuid
from unittest.mock import MagicMock

import pytest
import yaml

from app.models.rule import Rule
from scripts.seed_rules import seed_file

SEEDS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "seeds"
SUPPORTED_PRODUCT_LINES = {
    "global",
    "term",
    "ulip",
    "par",
    "non_par",
    "savings_endowment",
    "pension_annuity",
    "rider",
    "group",
}
SEED_FILES = sorted(SEEDS_DIR.glob("*.yaml"))


@pytest.mark.parametrize("seed_path", SEED_FILES, ids=lambda path: path.stem)
def test_every_seeded_rule_has_an_explicit_supported_product_line(seed_path):
    data = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
    missing = []
    unsupported = []

    for index, rule in enumerate(data.get("rules", []), start=1):
        label = f"rule {index}: {rule.get('rule_text', '<missing rule_text>')[:80]}"
        if "product_line" not in rule:
            missing.append(label)
        elif rule["product_line"] not in SUPPORTED_PRODUCT_LINES:
            unsupported.append(f"{label} ({rule['product_line']!r})")

    assert not missing, f"{seed_path.name} missing product_line:\n" + "\n".join(missing)
    assert not unsupported, f"{seed_path.name} unsupported product_line:\n" + "\n".join(unsupported)


def test_seed_rerun_versions_legacy_scope_instead_of_rewriting_history(tmp_path):
    seed_path = tmp_path / "rules.yaml"
    seed_path.write_text(
        yaml.safe_dump({
            "category": "regulatory",
            "rules": [{
                "rule_text": "A cross-product disclosure is mandatory.",
                "severity": "high",
                "product_line": "global",
            }],
        }),
        encoding="utf-8",
    )
    legacy = Rule(
        id=uuid.uuid4(),
        category="regulatory",
        rule_text="A cross-product disclosure is mandatory.",
        severity="high",
        is_active=True,
        product_line=None,
        version=1,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [legacy]
    added = []

    def add(rule):
        added.append(rule)

    def flush():
        for rule in added:
            if rule.id is None:
                rule.id = uuid.uuid4()

    db.add.side_effect = add
    db.flush.side_effect = flush

    inserted, versioned, retired = seed_file(db, seed_path)

    assert (inserted, versioned, retired) == (0, 1, 0)
    assert legacy.is_active is False
    assert legacy.superseded_by == added[0].id
    assert added[0].product_line == "global"
    assert added[0].version == 2
    assert added[0].is_active is True


def test_seed_supports_same_requirement_in_multiple_product_scopes(tmp_path):
    seed_path = tmp_path / "rules.yaml"
    seed_path.write_text(
        yaml.safe_dump({
            "category": "regulatory",
            "rules": [
                {
                    "rule_text": "Disclose mortality charges.",
                    "severity": "high",
                    "product_line": "ulip",
                },
                {
                    "rule_text": "Disclose mortality charges.",
                    "severity": "high",
                    "product_line": "term",
                },
            ],
        }),
        encoding="utf-8",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []
    added = []
    db.add.side_effect = added.append

    inserted, versioned, retired = seed_file(db, seed_path)

    assert (inserted, versioned, retired) == (2, 0, 0)
    assert {rule.product_line for rule in added} == {"ulip", "term"}


def test_seed_rerun_retires_scope_removed_from_multi_scope_requirement(tmp_path):
    seed_path = tmp_path / "rules.yaml"
    seed_path.write_text(
        yaml.safe_dump({
            "category": "regulatory",
            "rules": [{
                "rule_text": "Disclose mortality charges.",
                "severity": "high",
                "product_line": "ulip",
            }],
        }),
        encoding="utf-8",
    )
    ulip = Rule(
        id=uuid.uuid4(),
        category="regulatory",
        rule_text="Disclose mortality charges.",
        severity="high",
        is_active=True,
        product_line="ulip",
    )
    term = Rule(
        id=uuid.uuid4(),
        category="regulatory",
        rule_text="Disclose mortality charges.",
        severity="high",
        is_active=True,
        product_line="term",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [ulip, term]

    inserted, versioned, retired = seed_file(db, seed_path)

    assert (inserted, versioned, retired) == (0, 0, 1)
    assert ulip.is_active is True
    assert term.is_active is False
    assert term.superseded_by is None
    assert term.rule_metadata == {
        "lifecycle": "retired",
        "retired_reason": "seed_scope_removed",
    }
