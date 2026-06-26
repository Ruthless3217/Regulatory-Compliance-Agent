"""Generate docs/guidelines-docs/disclaimers.md FROM the disclaimer registry.

The registry (backend/data/disclaimers/*.json) is the single source of truth.
This emits a guidelines-style markdown doc carrying each disclaimer's verbatim
text, so it can be ingested by the EXISTING scripts/ingest_guidelines.py
pipeline (-> rules + rag_source_docs embeddings) for LLM-tier awareness.

Usage: python backend/scripts/build_disclaimer_guidelines_doc.py
This only writes the markdown. Running the ingest (which calls live embeddings
and the rule LLM) is a separate manual operator step.
"""
from __future__ import annotations

from pathlib import Path

from app.services.disclaimer.registry import DisclaimerRegistry

OUT = Path(__file__).resolve().parents[2] / "docs" / "guidelines-docs" / "disclaimers.md"
REG_DIR = Path(__file__).resolve().parents[1] / "data" / "disclaimers"


def render_markdown(registry: DisclaimerRegistry) -> str:
    lines = [
        "# Mandatory Disclaimers (Bajaj Life)",
        "",
        "Source: Copy of Disclaimers.xlsx. Generated from the disclaimer registry —",
        "do not edit by hand; edit the registry JSON and regenerate.",
        "",
    ]
    for d in sorted(registry.all(), key=lambda x: -x.precedence):
        lines.append(f"## {d.type}")
        lines.append("")
        lines.append(f"When required: {d.type} (trigger id `{d.id}`).")
        lines.append("")
        lines.append("Mandated text (verbatim):")
        lines.append("")
        lines.append("```")
        lines.append(d.text)
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    reg = DisclaimerRegistry(REG_DIR)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render_markdown(reg), encoding="utf-8")
    print(f"wrote {OUT} ({len(reg.all())} disclaimers)")


if __name__ == "__main__":
    main()
