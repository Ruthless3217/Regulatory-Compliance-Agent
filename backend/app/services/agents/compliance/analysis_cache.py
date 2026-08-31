"""Chunk-level analysis reuse: which chunks may skip their LLM passes.

A re-analysis re-grades every chunk of a document, including the ones nobody
touched — roughly three LLM calls per chunk (grade + completeness sweep + LLM
critic). When a chunk's text AND everything that shapes its grading are
identical to the last successfully persisted run, last run's verdicts are still
the verdicts. Carrying them forward is not an approximation of the answer, it
is the answer.

Two keys decide it, both stored on ``content_chunks`` (migration 0035):

``content_hash``
    sha256 of the chunk's EXACT text. Deliberately not normalised: a carried-
    forward finding quotes ``current_text`` verbatim out of the chunk, and the
    grounding guard (nodes.verify_evidence_grounding) drops any finding whose
    quote is not literally present. A case/whitespace-insensitive hash would
    let a reused finding cite text the document no longer contains.

``context_key``
    sha256 of everything else that chunk's grading depended on: the run
    fingerprint (prompt version, models, feature flags, retrieval config, and
    the rule / precedent / product corpus) plus the rendered cross-chunk
    document context. The document context is load-bearing — it embeds other
    chunks' text into this chunk's prompt, so without it in the key, editing
    chunk 7 would leave a stale verdict on chunk 2 that was graded while
    reading chunk 7's old text.

Both keys are stamped onto the chunk rows only when a run actually PERSISTS
(engine.persist_results). A degraded run persists nothing, so it must never
advertise a cache whose verdicts never reached the database.

Deliberately NOT in the key: ``scoring_policy_version``. Scoring runs after
analysis over the pooled violations of the whole document and is recomputed
every run, so a scoring change must not invalidate chunk grading.
"""
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Bump this string whenever a change could make the SAME chunk grade
# differently: any edit to the analysis prompts (preprocessing_service.
# create_precedent_prompts / create_completeness_sweep_prompt), to the critic
# prompt, or to the post-grading filters in graph/nodes.py (confidence floors,
# severity maps, dedupe, structural suppression). Forgetting to bump it means a
# re-analysis serves verdicts produced by the OLD logic. Date-ordered so the
# history reads chronologically; the value itself is opaque.
PROMPT_VERSION = "2026-08-11"


def sha256_hex(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def chunk_content_hash(text: str) -> str:
    """Hash of a chunk's exact text — the chunk half of the cache key."""
    return sha256_hex(text)


def _rule_fingerprint(rule: Dict[str, Any]) -> str:
    """Identity of one rule AS THE PROMPT SEES IT.

    (rule_id, rule_version) is the documented pair, but `version` is not carried
    on the serialized rule dicts in graph state and an edit that forgets to bump
    it would go unnoticed. Hashing the fields that reach the prompt catches both
    a version bump and a silent edit, without a DB round trip.
    """
    return "{}:{}".format(
        rule.get("id"),
        sha256_hex(
            "|".join(
                str(rule.get(k) or "")
                for k in ("rule_text", "severity", "category", "product_line")
            )
        )[:16],
    )


def run_context_fingerprint(
    settings: Any,
    active_rules: Optional[Dict[str, List[Dict]]] = None,
    retrieved_examples: Optional[Dict[str, List[Dict]]] = None,
    product_facts: Optional[List[Dict]] = None,
) -> str:
    """One fingerprint per run for everything that is not chunk-specific.

    Disclosure settings are excluded on purpose: disclosure_node is a
    document-level checker that re-runs every time and never feeds chunk
    grading.
    """
    s = settings
    parts: Dict[str, Any] = {
        "prompt_version": PROMPT_VERSION,
        "llm": [
            getattr(s, "llm_provider", ""),
            getattr(s, "llm_model", ""),
            getattr(s, "llm_reasoning_effort", ""),
        ],
        "critic": [
            getattr(s, "critic_llm_provider", "") or getattr(s, "llm_provider", ""),
            getattr(s, "critic_llm_model", "") or getattr(s, "llm_model", ""),
            getattr(s, "critic_llm_reasoning_effort", ""),
            bool(getattr(s, "critic_enabled", False)),
            bool(getattr(s, "llm_critic_enabled", False)),
        ],
        "flags": [
            bool(getattr(s, "completeness_sweep_enabled", False)),
            bool(getattr(s, "product_grounding_enabled", False)),
            bool(getattr(s, "cross_chunk_context_enabled", False)),
            getattr(s, "cross_chunk_context_token_budget", 0),
        ],
        "retrieval": [
            getattr(s, "pgvector_top_k", 0),
            getattr(s, "rag_top_k_analysis", 0),
            getattr(s, "rag_min_cosine", 0),
            getattr(s, "rag_min_ts_rank", 0),
            getattr(s, "rag_rrf_k", 0),
            getattr(s, "product_docs_top_k", 0),
        ],
        "rules": sorted(
            _rule_fingerprint(r)
            for rule_list in (active_rules or {}).values()
            for r in (rule_list or [])
        ),
        "precedents": sorted(
            {
                str(p.get("id"))
                for plist in (retrieved_examples or {}).values()
                for p in (plist or [])
                if p.get("id")
            }
        ),
        "products": sorted(
            {str(c.get("uin")) for c in (product_facts or []) if c.get("uin")}
        ),
    }
    return sha256_hex(json.dumps(parts, sort_keys=True, default=str))


def chunk_context_key(
    run_fingerprint: str,
    chunk_text: str,
    document_context: Optional[str],
    product_uins: Optional[List[str]] = None,
) -> str:
    """The per-chunk half: run fingerprint + this chunk's text + the exact
    document-context string rendered into its prompt + the product fact cards
    grounded in it.

    Grounding is per chunk, so two runs can agree on the run-level product set
    while disagreeing on which cards THIS chunk carried. The cards decide which
    product findings the chunk can emit at all, so a chunk whose grounding
    changed must re-grade rather than carry last run's verdicts forward.
    Omitted (None) reproduces the pre-chunk-grounding key exactly.
    """
    parts = [
        run_fingerprint,
        chunk_content_hash(chunk_text),
        sha256_hex(document_context or ""),
    ]
    if product_uins is not None:
        parts.append(sha256_hex("|".join(sorted(str(u) for u in product_uins))))
    return sha256_hex("|".join(parts))


def plan_chunk_reuse(
    current_keys: Dict[str, str],
    stored_keys: Dict[str, Optional[str]],
    prior_violations: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Decide, per chunk, whether last run's verdicts still stand.

    ``{chunk_id: carried-forward violations}`` for every hit. A chunk id absent
    from the result must be analysed normally. A chunk id present with an EMPTY
    list is a real hit — last run graded that chunk clean, and "no findings" is
    a verdict like any other.

    A chunk with no stored key (never analysed, or last analysed before this
    feature existed) can never hit: an empty carry-forward would otherwise read
    as "clean" and silently erase findings.
    """
    hits: Dict[str, List[Dict[str, Any]]] = {}
    for chunk_id, key in (current_keys or {}).items():
        stored = (stored_keys or {}).get(chunk_id)
        if key and stored and stored == key:
            hits[chunk_id] = list((prior_violations or {}).get(chunk_id) or [])
    return hits


def llm_calls_per_chunk(settings: Any) -> int:
    """LLM calls one chunk costs when it IS graded: the grade pass, the
    completeness sweep, and the LLM critic. An estimate — the critic only fires
    when the chunk produced findings, and a malformed grade response can add a
    corrective retry."""
    return (
        1
        + (1 if getattr(settings, "completeness_sweep_enabled", False) else 0)
        + (1 if getattr(settings, "llm_critic_enabled", False) else 0)
    )
