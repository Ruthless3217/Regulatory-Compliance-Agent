# backend/tests/services/disclaimer/test_registry.py
from pathlib import Path
from app.services.disclaimer.registry import DisclaimerRegistry

REAL_DIR = Path(__file__).resolve().parents[3] / "data" / "disclaimers"


def test_loads_all_ten_disclaimers():
    reg = DisclaimerRegistry(REAL_DIR)
    assert reg.loaded_ok is True
    assert len(reg.all()) == 10
    ids = {d.id for d in reg.all()}
    assert "ulip_risk" in ids
    ulip = reg.get("ulip_risk")
    assert ulip.severity == "critical"
    assert ulip.anchors and "INVESTMENT RISK" in ulip.anchors[0]
    assert ulip.present_threshold == 0.85


def test_missing_dir_is_not_loaded_ok(tmp_path):
    reg = DisclaimerRegistry(tmp_path / "does_not_exist")
    assert reg.loaded_ok is False
    assert reg.all() == []


def test_empty_dir_is_not_loaded_ok(tmp_path):
    reg = DisclaimerRegistry(tmp_path)  # exists but no json
    assert reg.loaded_ok is False
