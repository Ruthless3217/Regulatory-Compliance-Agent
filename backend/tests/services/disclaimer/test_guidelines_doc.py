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


def test_doc_fenced_blocks_carry_each_disclaimer_text_byte_exact():
    """Each disclaimer's text must appear as an exact (byte-identical) fenced code block.

    The whitespace-collapsed containment test above verifies presence; this test
    locks the STRUCTURAL invariant: the generator must fence each text verbatim so
    downstream ingest can extract it unambiguously.  Multi-line disclaimers (e.g.
    'participating') must keep their embedded newlines inside the fence.
    """
    import re
    reg = DisclaimerRegistry(REG_DIR)
    md = _load_build().render_markdown(reg)
    blocks = re.findall(r"```\n(.*?)\n```", md, re.DOTALL)
    assert blocks, "Expected at least one fenced block in the generated doc"
    for d in reg.all():
        assert d.text in blocks, f"{d.id}: verbatim text not present as an exact fenced block"
    part = reg.get("participating")        # a known multi-line disclaimer
    assert part is not None, "'participating' disclaimer must exist in registry"
    assert "\n" in part.text, "'participating' text must contain embedded newlines"
    assert part.text in blocks, "participating: multi-line text must be byte-exact in a fenced block"
