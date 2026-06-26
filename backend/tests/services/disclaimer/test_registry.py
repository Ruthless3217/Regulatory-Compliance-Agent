# backend/tests/services/disclaimer/test_registry.py
import json
from pathlib import Path

from app.services.disclaimer.registry import DisclaimerRegistry

REAL_DIR = Path(__file__).resolve().parents[3] / "data" / "disclaimers"


def _valid_disclaimer_dict(disc_id: str = "sample", severity: str = "high") -> dict:
    """A minimally-complete, correctly-shaped disclaimer record for tmp tests."""
    return {
        "id": disc_id,
        "type": "Sample Disclaimer",
        "text": "Some verbatim disclaimer text.",
        "anchors": [],
        "severity": severity,
        "altered_severity": "moderate",
        "triggers": {"product_lines": [], "keywords_regex": [], "llm_obligation_type": "sample"},
        "match": {"present_threshold": 0.85, "altered_threshold": 0.45},
        "precedence": 10,
        "source": "test",
    }


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
    # Prove the loader reads thresholds from the file rather than masking a
    # missing key with the 0.85 default: tax_123_80c declares 0.8.
    assert reg.get("tax_123_80c").present_threshold == 0.8


def test_missing_dir_is_not_loaded_ok(tmp_path):
    reg = DisclaimerRegistry(tmp_path / "does_not_exist")
    assert reg.loaded_ok is False
    assert reg.all() == []


def test_empty_dir_is_not_loaded_ok(tmp_path):
    reg = DisclaimerRegistry(tmp_path)  # exists but no json
    assert reg.loaded_ok is False
    assert reg.all() == []


def test_malformed_json_is_skipped_healthy_still_loads(tmp_path):
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    (tmp_path / "good.json").write_text(
        json.dumps(_valid_disclaimer_dict("good_one")), encoding="utf-8"
    )
    reg = DisclaimerRegistry(tmp_path)
    assert reg.loaded_ok is True
    ids = {d.id for d in reg.all()}
    assert ids == {"good_one"}


def test_invalid_severity_is_skipped(tmp_path):
    (tmp_path / "bad_sev.json").write_text(
        json.dumps(_valid_disclaimer_dict("bad_sev", severity="bogus")), encoding="utf-8"
    )
    (tmp_path / "ok.json").write_text(
        json.dumps(_valid_disclaimer_dict("ok_one")), encoding="utf-8"
    )
    reg = DisclaimerRegistry(tmp_path)
    ids = {d.id for d in reg.all()}
    assert "bad_sev" not in ids
    assert "ok_one" in ids
