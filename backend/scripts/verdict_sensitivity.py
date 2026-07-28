"""Black-box verdict sensitivity analysis (Jacobian-inspired, one variable at a time).

For a given creative text and disclaimer rule, perturb exactly ONE input
variable per experiment and record whether the deterministic verdict
(disclaimer/matcher.match_details) or its similarity moves. This approximates
"∂verdict/∂variable" for the deterministic tier of the engine. It never claims
gradients of the LLM: API models expose no internals, so LLM-tier sensitivity
(obligation backstop firing, prompt composition) is limited to controlled A/B
calls and is listed as a SEPARATE, optional experiment class — skipped unless
an LLM is configured, and clearly labeled black-box.

Usage:
    cd backend && PYTHONPATH=. python scripts/verdict_sensitivity.py [--json OUT]
    (add --text FILE to analyse your own creative text)

Output: per-perturbation records + an influence matrix (markdown to stdout,
optionally JSON). No verdict is ever *changed* by this tool — it only measures.
See VERDICT_EXPLAINABILITY.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.disclaimer.matcher import MatchDetails, match_details, normalize
from app.services.disclaimer.registry import DisclaimerRegistry
from app.services.disclaimer.triggers import ProductContext, deterministic_triggers

DEFAULT_TEXT = (
    "Bajaj Life Wealth Creator - invest in our Equity Growth Fund.\n\n"
    "Between 2022 and 2024 our equity-linked fund delivered an average of 22% "
    "per annum.\n\n"
    "Past performance is not indicative of future performance."
)


@dataclass
class Perturbation:
    variable_changed: str
    original_value: str
    changed_value: str
    original_verdict: str
    new_verdict: str
    original_confidence: float
    new_confidence: float
    confidence_delta: float
    selected_rule_before: str
    selected_rule_after: str
    verdict_changed: bool
    note: str = ""


def _verdict(reg: DisclaimerRegistry, rule_id: str, doc: str,
             present_t: Optional[float] = None, altered_t: Optional[float] = None,
             required_text: Optional[str] = None) -> Tuple[str, float, MatchDetails]:
    d = reg.get(rule_id)
    det = match_details(
        required_text if required_text is not None else d.text,
        d.anchors, doc,
        present_t if present_t is not None else d.present_threshold,
        altered_t if altered_t is not None else d.altered_threshold,
    )
    return det.status, det.similarity, det


def _strip_markdown(s: str) -> str:
    s = re.sub(r"[*_#>`]+", " ", s)
    s = re.sub(r"^\s*[-•·]\s+", "", s, flags=re.M)
    return re.sub(r"[ \t]+", " ", s)


def _letterspace(s: str) -> str:
    return " ".join(list(s.replace(" ", "")))


def run_experiments(reg: DisclaimerRegistry, base_doc: str, rule_id: str) -> List[Perturbation]:
    d = reg.get(rule_id)
    base_status, base_sim, base_det = _verdict(reg, rule_id, base_doc)
    out: List[Perturbation] = []

    def record(variable: str, orig_v: str, new_v: str,
               status: str, sim: float, rule_after: str = None, note: str = "") -> None:
        out.append(Perturbation(
            variable_changed=variable,
            original_value=orig_v, changed_value=new_v,
            original_verdict=base_status, new_verdict=status,
            original_confidence=round(base_sim, 3), new_confidence=round(sim, 3),
            confidence_delta=round(sim - base_sim, 3),
            selected_rule_before=rule_id, selected_rule_after=rule_after or rule_id,
            verdict_changed=status != base_status, note=note,
        ))

    # 1. Raw vs normalised input (does normalisation itself change the verdict?)
    s, sim, _ = _verdict(reg, rule_id, normalize(base_doc))
    record("text_normalisation", "raw text", "pre-normalised text", s, sim)

    # 2. Markdown removed vs retained
    s, sim, _ = _verdict(reg, rule_id, _strip_markdown(base_doc))
    record("markdown", "markdown retained", "markdown stripped", s, sim)

    # 3. Reviewer-comment text appended vs absent
    s, sim, _ = _verdict(reg, rule_id, base_doc +
                         "\n[Reviewer note: check disclaimer font size before release]")
    record("comments_in_input", "no comment text", "reviewer note appended", s, sim)

    # 4. Old brand name vs current brand name (swap current → legacy era)
    swapped = re.sub(r"\bBajaj(?:\s+Allianz)?\s+Life\b", "Bajaj Allianz Life", base_doc)
    s, sim, _ = _verdict(reg, rule_id, swapped)
    record("brand_name", "as-submitted brand", "legacy 'Bajaj Allianz Life'",
           s, sim, note="disclaimer rules carry no brand filter; expect no change")

    # 5. Product context present vs absent (UIN resolved vs not) — affects which
    # disclaimers are REQUIRED (triggers), not how this one matches.
    ctx_no = ProductContext(is_ulip=False, is_par=False, has_product=False)
    ctx_yes = ProductContext(is_ulip=True, is_par=False, has_product=True)
    req_no = set(deterministic_triggers(base_doc, ctx_no, reg))
    req_yes = set(deterministic_triggers(base_doc, ctx_yes, reg))
    record("uin_product_context", f"no product resolved → required={sorted(req_no)}",
           f"ULIP product resolved → required={sorted(req_yes)}",
           base_status, base_sim,
           note="changes the obligation SET (e.g. ulip_risk added), not this verdict")

    # 6-7. Threshold sensitivity (one at a time)
    for name, pt, at in (("present_threshold−0.10", d.present_threshold - 0.10, None),
                         ("present_threshold+0.10", d.present_threshold + 0.10, None),
                         ("altered_threshold−0.10", None, d.altered_threshold - 0.10),
                         ("altered_threshold+0.10", None, d.altered_threshold + 0.10)):
        s, sim, _ = _verdict(reg, rule_id, base_doc, present_t=pt, altered_t=at)
        record("similarity_threshold", f"present={d.present_threshold} altered={d.altered_threshold}",
               name, s, sim)

    # 8. Chunk-boundary stability: split the document at several points and
    # rejoin with '\n' exactly as disclosure_node does.
    for frac in (0.25, 0.5, 0.75):
        cut = int(len(base_doc) * frac)
        rejoined = base_doc[:cut] + "\n" + base_doc[cut:]
        s, sim, _ = _verdict(reg, rule_id, rejoined)
        record("chunk_boundary", "unsplit document", f"split+rejoined at {int(frac*100)}%",
               s, sim)

    # 9. Context ablation: disclaimer sentence alone, and document without it.
    sent = d.text
    if normalize(sent) in normalize(base_doc):
        s, sim, _ = _verdict(reg, rule_id, sent)
        record("surrounding_context", "full document", "disclaimer sentence only", s, sim)
        without = re.sub(re.escape("Past performance is not indicative of future performance."),
                         "", base_doc, flags=re.IGNORECASE)
        s, sim, _ = _verdict(reg, rule_id, without)
        record("disclaimer_presence", "disclaimer present", "disclaimer removed",
               s, sim, note="ground-truth flip: verdict SHOULD change here")

    # 10. Rule wording punctuation variants (smart quotes / footnote markers on
    # the RULE side — the historic '**' contamination).
    for label, req in (("rule text with '**' prefix", "**" + d.text),
                       ("rule text with smart quotes", "“" + d.text + "”")):
        s, sim, _ = _verdict(reg, rule_id, base_doc, required_text=req)
        record("rule_wording_punctuation", "clean registry text", label, s, sim)

    # 11. Exact-match fast path disabled (force windowed metric only)
    doc_no_exact = base_doc.replace("indicative", "indicativ­e")  # soft hyphen breaks substring, not meaning
    s, sim, _ = _verdict(reg, rule_id, doc_no_exact)
    record("exact_match_path", "exact normalised substring available",
           "substring broken by soft hyphen (windowed path only)", s, sim,
           note="verdict must stay 'present' via windowed similarity")

    # 12. Alternative similarity metric (token_set_ratio instead of partial_ratio)
    from rapidfuzz import fuzz
    ts = fuzz.token_set_ratio(normalize(d.text), normalize(base_doc)) / 100.0
    record("similarity_method", "partial_ratio (windowed)", "token_set_ratio (bag of tokens)",
           base_status, ts, note="metric comparison only; engine uses windowed partial_ratio")

    # 13. OCR letter-spacing artefact on the disclaimer sentence
    if normalize(sent) in normalize(base_doc):
        spaced = base_doc.replace("Past performance is not indicative of future performance.",
                                  _letterspace("Past performance is not indicative of future performance."))
        s, sim, _ = _verdict(reg, rule_id, spaced)
        record("ocr_letterspacing", "clean sentence", "letter-spaced sentence",
               s, sim, note="extraction-quality dependence; fix belongs in OCR, not matcher")

    return out


def influence_matrix(perts: List[Perturbation]) -> List[dict]:
    rows = {}
    for p in perts:
        r = rows.setdefault(p.variable_changed, {
            "variable": p.variable_changed, "experiments": 0,
            "verdict_flips": 0, "max_confidence_delta": 0.0,
        })
        r["experiments"] += 1
        r["verdict_flips"] += int(p.verdict_changed)
        r["max_confidence_delta"] = max(r["max_confidence_delta"], abs(p.confidence_delta))
    ranked = sorted(rows.values(),
                    key=lambda r: (r["verdict_flips"], r["max_confidence_delta"]),
                    reverse=True)
    for r in ranked:
        r["verdict_impact"] = "high" if r["verdict_flips"] else (
            "medium" if r["max_confidence_delta"] > 0.1 else "low")
    return ranked


def main() -> None:
    # Windows consoles default to cp1252; keep the report printable everywhere.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", help="file with creative text (default: built-in example)")
    ap.add_argument("--rule", default="past_performance")
    ap.add_argument("--json", help="write full records to this JSON file")
    args = ap.parse_args()

    base_doc = Path(args.text).read_text(encoding="utf-8") if args.text else DEFAULT_TEXT
    reg = DisclaimerRegistry(Path(__file__).resolve().parents[1] / "data" / "disclaimers")
    if not reg.loaded_ok:
        raise SystemExit("disclaimer registry failed to load")

    perts = run_experiments(reg, base_doc, args.rule)
    base_status, base_sim, base_det = _verdict(reg, args.rule, base_doc)

    print(f"BASE VERDICT: {base_status} (similarity {base_sim:.3f}, "
          f"method {base_det.match_method}, reason {base_det.reason})\n")
    print(f"{'variable':26s} {'change':46s} {'verdict':22s} {'Δsim':>7s}")
    print("-" * 108)
    for p in perts:
        v = f"{p.original_verdict}→{p.new_verdict}" + (" ⚠FLIP" if p.verdict_changed else "")
        print(f"{p.variable_changed:26s} {p.changed_value[:46]:46s} {v:22s} {p.confidence_delta:+7.3f}")

    print("\nINFLUENCE MATRIX (ranked)")
    print(f"{'variable':26s} {'verdict impact':15s} {'flips':>5s} {'max |Δsim|':>10s}")
    print("-" * 62)
    for r in influence_matrix(perts):
        print(f"{r['variable']:26s} {r['verdict_impact']:15s} {r['verdict_flips']:5d} "
              f"{r['max_confidence_delta']:10.3f}")

    print("\nLLM-tier experiments (obligation backstop A/B, prompt-composition "
          "ablations) require a configured LLM endpoint and are executed by the "
          "eval harness, not this offline tool — see VERDICT_EXPLAINABILITY.md.")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"base": {"verdict": base_status, "similarity": base_sim},
                        "perturbations": [asdict(p) for p in perts],
                        "influence_matrix": influence_matrix(perts)}, indent=2),
            encoding="utf-8")
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
