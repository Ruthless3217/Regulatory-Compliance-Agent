"""Document-wide verbatim matching for mandated disclaimers.

Normalizes both sides, then picks the similarity metric by length: when the
document is at least as long as the required text (the common case — a small
footer disclaimer embedded in a much larger marketing document), a windowed
partial ratio locates the best-matching span; when the document is a fragment
shorter than the required text, a direct ratio is used instead. Anchors are
mandatory substrings that must survive even when the body matches.

Every classification is evidence-gated and explainable (2026-07-28, see
ROOT_CAUSE_ANALYSIS.md):

- "altered" requires a genuine aligned span sharing more than half of the
  required text's distinctive tokens. fuzz.partial_ratio plateaus around
  0.45-0.55 on unrelated marketing prose (shared stopwords/"performance"-type
  words), and that fuzzy floor used to be reported as "present but altered" —
  turning "we could not find the disclaimer" into "your wording is wrong".
- "present" additionally requires every legally-critical token of the required
  wording (negations, "guaranteed", "risk", …) to survive in the matched span;
  a near-verbatim copy that drops "not" reads ~0.96 similar but inverts the
  legal meaning, and must surface as "altered", never "present".
- ``match_details`` returns the full evidence: matched span, raw + normalised
  similarity, token overlap, method and a human-readable decision trace, so a
  verdict can always answer "why?".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

from rapidfuzz import fuzz

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")

# Function words safe to ignore when measuring whether an aligned span is a
# genuine attempt at the disclaimer. Legally meaningful words (not, no, may,
# guaranteed, risk, subject, past, future, …) are deliberately NOT here.
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "to", "in", "for", "and", "or", "as", "by", "on", "at", "it",
    "this", "that", "these", "those", "with", "shall", "will", "please",
})

# Tokens whose loss inverts or weakens the legal meaning of a disclaimer.
# If the required wording contains one of these and the matched span does not,
# the disclaimer is tampered — regardless of how high the fuzzy score is.
_LEGALLY_CRITICAL = frozenset({
    "not", "no", "never", "may", "guaranteed", "guarantee", "risk", "risks",
    "subject", "past", "future",
})


def normalize(s: str) -> str:
    """Lowercase, strip punctuation/zero-width noise, collapse whitespace."""
    s = (s or "").lower().replace("​", "").replace("﻿", "")
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


@dataclass(frozen=True)
class MatchDetails:
    """Full evidence behind one disclaimer classification."""
    status: str                    # present | altered | missing
    similarity: float              # on normalised text (drives thresholds)
    raw_similarity: float          # same metric on raw (pre-normalisation) text
    match_method: str              # exact_normalized_match | windowed_partial_ratio | direct_ratio
    evidence_span: str             # best-aligned window of the normalised document
    token_overlap: float           # distinctive required tokens present in the span
    reason: str                    # exact_match | verbatim_window | anchor_absent |
                                   # critical_token_lost | partial_attempt |
                                   # fuzzy_floor_noise | no_match
    critical_tokens_missing: List[str] = field(default_factory=list)
    decision_trace: List[str] = field(default_factory=list)


def _distinctive_tokens(required_norm: str) -> frozenset:
    return frozenset(t for t in required_norm.split() if t not in _STOPWORDS)


def _raw_similarity(required_text: str, document_text: str) -> float:
    a = (required_text or "").lower()
    b = (document_text or "").lower()
    if not a or not b:
        return 0.0
    if len(b) >= len(a):
        return fuzz.partial_ratio(a, b) / 100.0
    return fuzz.ratio(a, b) / 100.0


def _aligned_window(required_norm: str, doc_norm: str) -> Tuple[float, str, str]:
    """Return (similarity, evidence_span, method) for the best alignment."""
    if len(doc_norm) >= len(required_norm):
        al = fuzz.partial_ratio_alignment(required_norm, doc_norm)
        if al is None:
            return 0.0, "", "windowed_partial_ratio"
        return al.score / 100.0, doc_norm[al.dest_start:al.dest_end], "windowed_partial_ratio"
    return fuzz.ratio(required_norm, doc_norm) / 100.0, doc_norm, "direct_ratio"


def _anchors_present(anchors: List[str], doc_norm: str) -> bool:
    for a in anchors:
        if normalize(a) not in doc_norm:
            return False
    return True


def match_details(
    required_text: str,
    anchors: List[str],
    document_text: str,
    present_threshold: float,
    altered_threshold: float,
) -> MatchDetails:
    doc_norm = normalize(document_text)
    req_norm = normalize(required_text)
    raw_sim = _raw_similarity(required_text, document_text)
    trace: List[str] = []
    declared_anchors = anchors or []
    anchors_ok = _anchors_present(declared_anchors, doc_norm)

    if not req_norm or not doc_norm:
        return MatchDetails(
            status="missing", similarity=0.0, raw_similarity=raw_sim,
            match_method="windowed_partial_ratio", evidence_span="",
            token_overlap=0.0, reason="no_match",
            decision_trace=["empty required text or document"],
        )

    # 1. Exact normalised substring — deterministic, no fuzzy metric involved.
    if req_norm in doc_norm and anchors_ok:
        trace.append("normalised required text found verbatim in document")
        return MatchDetails(
            status="present", similarity=1.0, raw_similarity=raw_sim,
            match_method="exact_normalized_match", evidence_span=req_norm,
            token_overlap=1.0, reason="exact_match", decision_trace=trace,
        )

    # 2. Locate the best-matching window and measure the evidence in it.
    sim, span, method = _aligned_window(req_norm, doc_norm)
    distinctive = _distinctive_tokens(req_norm)
    span_tokens = set(span.split())
    overlap = (
        len(distinctive & span_tokens) / len(distinctive) if distinctive else 0.0
    )
    trace.append(
        f"{method}: similarity {sim:.3f}, best span {span[:80]!r}, "
        f"distinctive-token overlap {overlap:.2f}"
    )

    # 3. A declared statutory anchor is ABSENT — the disclaimer's defining
    # content (e.g. the ULIP investment-risk line) is not in the document.
    # "altered" (present-but-tampered) ONLY when the body is otherwise strongly
    # present; a high partial-ratio overlap with unrelated text must NEVER
    # soften a genuine omission of a mandated statutory line.
    if declared_anchors and not anchors_ok:
        if sim >= present_threshold:
            trace.append("statutory anchor absent while body strongly present → altered")
            return MatchDetails(
                status="altered", similarity=sim, raw_similarity=raw_sim,
                match_method=method, evidence_span=span, token_overlap=overlap,
                reason="anchor_absent", decision_trace=trace,
            )
        trace.append("statutory anchor absent and body weak → missing")
        return MatchDetails(
            status="missing", similarity=sim, raw_similarity=raw_sim,
            match_method=method, evidence_span=span, token_overlap=overlap,
            reason="no_match", decision_trace=trace,
        )

    # 4. Strong body match — but a fuzzy score cannot vouch for legally-critical
    # tokens: "past performance IS indicative…" reads ~0.96 similar to the
    # approved line while inverting its meaning.
    if sim >= present_threshold:
        required_critical = distinctive & _LEGALLY_CRITICAL
        lost = sorted(required_critical - span_tokens)
        if lost:
            trace.append(f"legally-critical token(s) {lost} missing from span → altered")
            return MatchDetails(
                status="altered", similarity=sim, raw_similarity=raw_sim,
                match_method=method, evidence_span=span, token_overlap=overlap,
                reason="critical_token_lost", critical_tokens_missing=lost,
                decision_trace=trace,
            )
        trace.append("similarity above present threshold, all critical tokens intact")
        return MatchDetails(
            status="present", similarity=sim, raw_similarity=raw_sim,
            match_method=method, evidence_span=span, token_overlap=overlap,
            reason="verbatim_window", decision_trace=trace,
        )

    # 5. Mid-band: only a span that genuinely attempts the disclaimer counts as
    # "altered". fuzz.partial_ratio yields ~0.45-0.55 against unrelated prose
    # (the fuzzy floor) — that is absence, not alteration.
    if sim >= altered_threshold:
        if overlap > 0.5:
            trace.append("mid-band similarity with majority token overlap → altered")
            return MatchDetails(
                status="altered", similarity=sim, raw_similarity=raw_sim,
                match_method=method, evidence_span=span, token_overlap=overlap,
                reason="partial_attempt", decision_trace=trace,
            )
        trace.append(
            "mid-band similarity is fuzzy-floor noise (token overlap ≤ 0.5) → missing"
        )
        return MatchDetails(
            status="missing", similarity=sim, raw_similarity=raw_sim,
            match_method=method, evidence_span=span, token_overlap=overlap,
            reason="fuzzy_floor_noise", decision_trace=trace,
        )

    trace.append("similarity below altered threshold → missing")
    return MatchDetails(
        status="missing", similarity=sim, raw_similarity=raw_sim,
        match_method=method, evidence_span=span, token_overlap=overlap,
        reason="no_match", decision_trace=trace,
    )


def classify(
    required_text: str,
    anchors: List[str],
    document_text: str,
    present_threshold: float,
    altered_threshold: float,
) -> Tuple[str, float]:
    d = match_details(
        required_text, anchors, document_text, present_threshold, altered_threshold
    )
    return d.status, d.similarity
