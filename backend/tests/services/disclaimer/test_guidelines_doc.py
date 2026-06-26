# backend/tests/services/disclaimer/test_guidelines_doc.py
from pathlib import Path
from app.services.disclaimer.registry import DisclaimerRegistry
import importlib.util

REG_DIR = Path(__file__).resolve().parents[3] / "data" / "disclaimers"
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "build_disclaimer_guidelines_doc.py"


def _load_build():
    spec = importlib.util.spec_from_file_location("build_disc_doc", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_doc_contains_every_disclaimer_verbatim():
    reg = DisclaimerRegistry(REG_DIR)
    md = _load_build().render_markdown(reg)
    assert md.lstrip().startswith("#")
    for d in reg.all():
        assert d.type in md
        # full verbatim text present (whitespace-normalized containment)
        assert " ".join(d.text.split()) in " ".join(md.split())
    # the statutory ULIP anchor survives verbatim
    assert "INVESTMENT RISK IN INVESTMENT PORTFOLIO IS BORNE BY THE POLICYHOLDER" in md
