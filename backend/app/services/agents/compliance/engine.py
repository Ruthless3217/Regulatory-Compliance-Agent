"""
ComplianceEngine: Main entry point for compliance analysis.
Orchestrates the LangGraph workflow and persists results.

Follows 12-Factor Agent principles:
- Acts as a Stateless Reducer: State_n+1 = f(State_n, Input)
"""
import logging
import traceback
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from datetime import datetime, timezone

try:
    from langsmith import traceable
except Exception:  # pragma: no cover
    def traceable(*_a, **_kw):  # type: ignore
        def _d(fn): return fn
        return _d if not (_a and callable(_a[0])) else _a[0]

from app.models.submission import Submission
from app.models.compliance_check import ComplianceCheck
from app.models.violation import Violation
from app.schemas.compliance_schemas import ComplianceAnalysisResult
from app.services.agents.compliance.scoring import scoring_service
# The one classifier both sides need: the graph writes `product_unresolved`,
# the gate below decides what it means. Keeping it in one place is what stops
# the two drifting apart. nodes.py does not import this module, so no cycle.
from app.services.agents.graph import nodes as graph_nodes
from app.services.violation_serializer import (
    finding_counts,
    latest_feedback_map,
    serialize_violation,
)

logger = logging.getLogger(__name__)

# Must not exceed violations.category's column width (models/violation.py).
# Widened 50 -> 100 by migration 0032; kept here as a hard write-side clamp so
# the pipeline degrades to a truncated label instead of losing an entire run.
_CATEGORY_MAX_LEN = 100


class ComplianceEngine:
    """
    Core compliance analysis engine.
    Orchestrates the LangGraph workflow and persists results.
    """

    # Degraded reasons that block a passing grade but are not hard failures —
    # the document could not be substantively evaluated, so it needs review
    # rather than being marked "failed".
    _NEEDS_REVIEW_REASONS = {
        "knowledge_base_empty",
        "no_grounded_evidence",
        "analysis_incomplete",
        "rag_degraded",
        "rules_unavailable",
        "disclosure_unavailable",
        "product_ambiguous",
        "product_unresolved",
        "product_resolution_failed",
        "scope_metadata_missing",
        "disclosure_recall_degraded",
    }

    @staticmethod
    def evaluate_persistability(final_state: Dict[str, Any]) -> tuple:
        """Decide whether a graph run may be persisted as a real (gradeable)
        compliance result.

        Returns (can_persist, block_reason). A run is NOT persistable when it
        produced no analyzable content, the graph failed, the dispatch/analysis
        nodes flagged degradation, or any chunk failed to grade. This is the
        load-bearing fail-closed guard: an unevaluated document must never be
        recorded as 100/A/passed. See docs/architect-audit-2026-05-30.md (C1).
        """
        chunks = final_state.get("chunks") or []
        status = final_state.get("status")
        md = final_state.get("metadata") or {}

        if not chunks:
            return False, "no_content"
        if status == "failed":
            return False, "failed"
        # Safety-critical product-grounding signals are checked independently
        # of the single legacy degraded slot so another warning cannot mask
        # them via setdefault/overwrite ordering.
        if md.get("product_resolution_failed"):
            return False, "product_resolution_failed"
        if md.get("product_ambiguous_uins"):
            return False, "product_ambiguous"
        # Only the SCOPE sub-signal refuses. `product_unresolved` is a bucket of
        # four different things, and treating them alike conflated "we cannot
        # prove which rules apply" with "one named product's own record is
        # missing". The first is unprovable; the second is a bounded gap in a
        # document whose scope is proven, and it is reported as a warning with
        # the UIN and the sections it occurs in (graph/nodes._add_warning).
        # Measured on submission 8a3c2db4: scope {ulip} resolved from two fully
        # grounded products, the gap confined to 1 of 27 chunks, 38 findings
        # discarded. See tests/test_partial_analysis_unresolved_products.py.
        if graph_nodes.scope_is_unprovable(md):
            return False, "product_unresolved"
        # The disclosure sweep only covered part of the document (or the LLM
        # backstop failed outright), so required-disclaimer findings are
        # silently missing. Fail closed: review it, don't grade it.
        if md.get("disclosure_recall_degraded"):
            return False, "disclosure_recall_degraded"
        degraded = md.get("degraded")
        if degraded:
            return False, degraded
        if md.get("analysis_failed_chunks"):
            return False, "analysis_incomplete"
        # The floor. Precedent absence and an untagged rule corpus are now
        # warnings, so this is what stops them adding up to a grade with no
        # grounding behind it: with no applicable rule, no precedent and no
        # fact card, the only tier that ran is `novel` — the model's own
        # judgment — and that is an opinion, not a regulatory determination.
        # Judged only when dispatch actually reported the census; inventing a
        # refusal from an unset key would fail closed on absent data rather
        # than on evidence.
        if "grounded_evidence" in md and not ComplianceEngine._grounded_tier_count(
            md["grounded_evidence"]
        ):
            return False, "no_grounded_evidence"
        return True, None

    # The tiers whose presence makes a verdict a grounded determination rather
    # than the model's own opinion. A tier this build does not know about does
    # NOT clear the floor: adding one is a deliberate act, and failing closed on
    # an unrecognised census is the safe direction.
    _GROUNDED_TIERS = ("rules", "precedents", "product_facts")

    @staticmethod
    def _grounded_tier_count(evidence: Any) -> int:
        """How much grounded evidence the census actually reports.

        Strict on purpose. `any(evidence.values())` was truthy for the string
        "0", for an unknown key, for a negative count, for `True`, and for a
        non-dict — five ways to clear a floor whose only job is to stop an
        ungrounded run being graded. Anything unreadable counts as zero, so a
        malformed census refuses instead of passing through.
        """
        if not isinstance(evidence, dict):
            return 0
        return sum(
            value
            for key, value in evidence.items()
            if key in ComplianceEngine._GROUNDED_TIERS
            # bool is an int subclass; True must not stand in for a count.
            and isinstance(value, int) and not isinstance(value, bool)
            and value > 0
        )

    # A run that reached a determination with named limitations must never be
    # recorded as a clean pass: "not evaluated" is not "compliant". The SCORE is
    # left alone deliberately — deducting points for absent evidence would
    # fabricate a compliance judgment, which is the opposite error. It is the
    # verdict that refuses to certify.
    _UNCERTIFIABLE_STATUS = "flagged"

    @staticmethod
    def cap_status_for_warnings(status: str, warnings: Optional[List[Any]]) -> str:
        if not warnings:
            return status
        return (
            ComplianceEngine._UNCERTIFIABLE_STATUS
            if status == "passed" else status
        )

    # Whitelisted observability keys persisted onto analysis_runs.run_metadata.
    # Everything else in graph state (chunks, prompts, messages) stays out —
    # the column is for "why did retrieval/grading behave this way", not a dump.
    _RUN_METADATA_KEYS = (
        "retrieval_debug", "grounding_mix", "product_match",
        "rag_degraded", "degraded", "analysis_failed_chunks",
        "product_ambiguous_uins",
        "product_unresolved",
        "product_resolution_failed",
        "scope_metadata_missing",
        "knowledge_base_empty",
        "declared_product_line",
        # Named limitations of a run that still reached a determination, and
        # the evidence census the persistability floor is judged on. Both are
        # load-bearing for the audit: they are the record of what the grade
        # does and does not cover.
        "analysis_warnings",
        "grounded_evidence",
        # A global submission graded under the narrower scope its detected
        # products imply — not a refusal, but the audit must show it.
        "scope_narrowed_from_global",
        # Products identified but not injected into the prompt (scope still
        # covers them). Never let a grounding cap be a silent loss.
        "product_grounding_budget",
        # Which product was grounded in which chunk (chunk-aware grounding).
        "product_grounding_chunks",
        "disclosure_recall_degraded", "rag_rules_per_chunk", "precedents_per_chunk",
        # Chunk-level analysis reuse (analysis_cache.py): how much of this run
        # was carried forward, and the context fingerprint it was graded under —
        # the one field that says WHY a later run did or didn't reuse it. The
        # per-chunk `chunk_keys` map is deliberately absent: it is plumbing for
        # persist_results, not run observability.
        "chunks_total", "chunks_reused", "chunks_analyzed", "llm_calls_saved",
        "analysis_context_key",
    )

    @staticmethod
    def run_metadata_from_state(final_state: dict) -> dict:
        """Compact, whitelisted extract of graph-state metadata for durable
        run-level observability (retrieval debugger — RETRIEVAL_RCA.md §4)."""
        md = (final_state or {}).get("metadata") or {}
        out = {}
        for key in ComplianceEngine._RUN_METADATA_KEYS:
            value = md.get(key)
            if value not in (None, {}, []):
                out[key] = value
        return out

    @staticmethod
    @traceable(run_type="chain", name="ComplianceEngine.analyze_submission")
    async def analyze_submission(submission_id: str, db: Session, user=None, session_id: str = None) -> Optional[ComplianceCheck]:
        """
        Entry point for compliance analysis using LangGraph.
        
        Args:
            submission_id: UUID of the submission to analyze
            db: Database session
            
        Returns:
            ComplianceCheck if the run was gradeable and persisted; None if the
            run was not persistable (status set to 'needs_review' for degraded
            runs or 'failed' for hard failures — see evaluate_persistability).
        """
        submission = None
        try:
            # 1. Load submission under a row lock so two concurrent triggers
            #    can't both start an analysis (TOCTOU on status → duplicate
            #    checks + doubled LLM spend). The second waiter blocks here,
            #    then sees status="analyzing" and bails.
            submission = (
                db.query(Submission)
                .filter(Submission.id == submission_id)
                .with_for_update()
                .first()
            )
            if not submission:
                raise ValueError(f"Submission {submission_id} not found")

            if submission.status == "analyzing":
                from app.config import settings
                from app.services.run_tracker import find_stale_running_run

                stale_run = await find_stale_running_run(
                    db, str(submission_id), settings.stale_analysis_run_minutes
                )
                if stale_run is None:
                    logger.warning(
                        f"Submission {submission_id} is already being analyzed; "
                        f"skipping duplicate trigger (idempotency guard)."
                    )
                    db.commit()  # release the row lock
                    return None

                # Orphaned: the process running that run was killed mid-flight
                # (container restart, OOM, --reload) so its `except Exception`
                # handler never ran to flip the status back. Close the dead run
                # and fall through to claim the submission for this attempt.
                logger.warning(
                    f"Reclaiming orphaned analysis run {stale_run.id} for "
                    f"submission {submission_id} (stale > "
                    f"{settings.stale_analysis_run_minutes}m, no finished_at)."
                )
                stale_run.status = "failed"
                stale_run.finished_at = datetime.now(timezone.utc)
                stale_run.degraded_reason = "orphaned_stale_run_reclaimed"
                db.add(stale_run)

            # 2. Claim the submission. Commit releases the lock so readers see
            #    'analyzing' immediately.
            submission.status = "analyzing"
            db.commit()

            from app.services.run_tracker import open_run, close_run
            from app.services.observability.usage_context import set_usage_context, reset_usage_context
            
            run = await open_run(db, str(submission_id), user, session_id or "local")
            set_usage_context(user_id=str(user.id) if user else None, session_id=session_id or "local", submission_id=str(submission_id), run_id=str(run.id), feature="compliance_analysis")

            # 3. Initialize Graph Context
            from app.services.agents.orchestrator import orchestrator
            from app.services.agents.graph.context import GraphContext

            token = GraphContext.set_db_session(db)

            # 4. Build initial state
            initial_state = {
                "submission_id": str(submission_id),
                "user_id": str(submission.submitted_by) if submission.submitted_by else None,
                "chunks": [],
                "active_rules": {},
                "chunk_rules": {},
                "violations": [],
                "active_agents": [],
                "scores": {},
                "status": "running",
                "messages": [],
                "metadata": {
                    "declared_product_line": submission.product_line,
                },
                "user_feedback": None
            }

            # 5. Config for persistence (thread_id enables HITL checkpointing).
            #    The thread id is per-RUN, not per-submission: ComplianceState
            #    .violations is an `operator.add` reducer channel, so re-running
            #    a submission on the same thread RESUMED the finished checkpoint
            #    and appended a second copy of every violation (3 -> 6 -> 9).
            # ponytail: old threads are never deleted — unbounded checkpoint
            # growth in Redis. Cleanup path: pass a TTL to AsyncRedisSaver in
            # orchestrator._ensure_checkpointer_setup (MemorySaver is per-process
            # so it dies with the container anyway).
            config = {
                "configurable": {"thread_id": f"{submission_id}:{run.id}"},
                "metadata": {
                    "submission_id": str(submission_id),
                    "user_id": str(submission.submitted_by) if submission.submitted_by else None
                }
            }

            try:
                logger.info(f"Starting LangGraph analysis for submission {submission_id}")

                # No HITL interrupt — the graph runs straight through to END, so
                # this returns the fully analyzed final state in one call.
                final_state = await orchestrator.run_workflow(initial_state, config=config)

                logger.info("LangGraph execution COMPLETED.")

                # FAIL CLOSED: never persist a passing grade for a run that
                # could not be substantively evaluated (no chunks, graph
                # failure, degraded retrieval, or any chunk that failed to
                # grade). A degraded document needs human review; a hard
                # failure is marked failed. See architect-audit C1.
                can_persist, block_reason = ComplianceEngine.evaluate_persistability(final_state)
                if not can_persist:
                    needs_review = block_reason in ComplianceEngine._NEEDS_REVIEW_REASONS
                    submission.status = "needs_review" if needs_review else "failed"
                    logger.error(
                        f"Refusing to persist gradeable result for submission "
                        f"{submission_id}: reason={block_reason}. "
                        f"Marking submission '{submission.status}' (NOT graded)."
                    )
                    db.commit()
                    await close_run(db, run, {
                        "status": submission.status, "error": block_reason,
                        "run_metadata": ComplianceEngine.run_metadata_from_state(final_state),
                    }, user)
                    return None

                # 6. Persist results. The submission status flip to 'analyzed'
                #    happens INSIDE persist_results, in the SAME transaction as
                #    the check + violations, so a crash can't leave a fully
                #    graded check attached to a submission still reading
                #    'analyzing' (torn audit record).
                compliance_check = ComplianceEngine.persist_results(
                    submission_id=str(submission_id),
                    violations=final_state.get("violations", []),
                    scores=final_state.get("scores", {}),
                    db=db,
                    analysis_run_id=str(run.id),
                    chunk_keys=(final_state.get("metadata") or {}).get("chunk_keys"),
                    analysis_warnings=(
                        (final_state.get("metadata") or {}).get("analysis_warnings")
                    ),
                )

                # 7. Flip RAG chunk status to 'analyzed' so they become eligible
                # for cross-submission similarity search. Non-fatal on failure.
                try:
                    from app.services.rag.indexers.chunks_indexer import mark_submission_analyzed
                    summary = (
                        f"{submission.title} · score {compliance_check.overall_score}"
                        f" · grade {compliance_check.grade}"
                    )
                    await mark_submission_analyzed(
                        submission_id=str(submission_id), db=db, summary=summary
                    )
                except Exception as e:
                    logger.warning(f"RAG mark-analyzed failed (non-fatal): {e}")

                await close_run(db, run, {
                    "status": "completed", "check_id": str(compliance_check.id),
                    "run_metadata": ComplianceEngine.run_metadata_from_state(final_state),
                }, user)
                return compliance_check

            finally:
                # Reset the request-scoped DB session ContextVar so it does not
                # leak a (now closing) session into whatever task reuses this
                # context next. Previously this block was dead (`pass`), so the
                # token was never reset.
                GraphContext.reset(token)
                try:
                    reset_usage_context()
                except NameError:
                    pass

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error analyzing submission {submission_id}: {str(e)}")
            db.rollback()
            if submission:
                try:
                    submission.status = "failed"
                    db.add(submission)
                    db.commit()
                    if 'run' in locals():
                        from app.services.run_tracker import close_run
                        await close_run(db, locals()['run'], {"status": "failed", "error": str(e)}, user)
                except Exception:
                    pass
            raise

    @staticmethod
    def _reparent_reused(
        v_data: Dict, check_id, analysis_run_id: Optional[str], db: Session
    ) -> bool:
        """Move a carried-forward finding onto the new check instead of copying it.

        A chunk that skipped its LLM passes (analysis_cache.py) contributes the
        SAME rows it produced last run. They are re-parented, not duplicated:
        rule_feedback verdicts, reviewer actions and the review_status all key on
        `violations.id`, so a copy would hand the reviewer a fresh finding and
        silently drop the decision they already made on it — the same reasoning
        the scoped-rerun endpoint re-parents its out-of-scope rows.

        Returns False when the row has vanished, so the caller falls back to
        inserting it fresh (fail open: a lost verdict beats a lost finding).
        """
        reused_id = v_data.get("reused_violation_id")
        if not reused_id:
            return False
        row = db.query(Violation).filter(Violation.id == reused_id).first()
        if row is None:
            logger.warning(
                "Reused violation %s no longer exists; re-inserting it as a new "
                "finding (its reviewer verdict, if any, does not carry over).",
                reused_id,
            )
            return False
        row.compliance_check_id = check_id
        row.analysis_run_id = analysis_run_id
        # A chunk can keep its text but move; position follows the current chunk.
        row.chunk_index = v_data.get("chunk_index")
        if v_data.get("location"):
            row.location = v_data["location"]
        db.add(row)
        return True

    # Every anchor field a finding can carry, all absent by default. Returned
    # whole (rather than only the keys that were resolved) so a partially
    # located finding cannot inherit a previous one's coordinates.
    _NO_ANCHOR = {
        "section_title": None,
        "anchor_node_key": None,
        "anchor_offset_start": None,
        "anchor_offset_end": None,
        "anchor_fingerprint": None,
    }

    @staticmethod
    def _anchor_fields(current_text: Optional[str], chunk) -> Dict[str, Any]:
        """Where in the EDITABLE document this finding sits.

        The chunk was cut along the editor's own blocks
        (preprocessing_service._chunk_by_blocks), so its metadata carries the id
        of every block it is made of. The block holding the quoted text is the
        finding's anchor, and the offsets are measured in that block's
        NORMALIZED text — the coordinate system `findingAnchor.verifiedOffsets`
        checks them in.

        A quote found in NO block leaves `anchor_node_key` NULL. Naming the
        chunk's first block instead would be a guess, and the frontend acts on
        the id before it verifies anything: a wrong one scopes the search to the
        wrong paragraph and turns a locatable finding into an unlocated one.
        NULL simply starts the search document-wide, which is what every
        finding did before block ids existed.
        """
        out = dict(ComplianceEngine._NO_ANCHOR)
        if chunk is None:
            return out
        metadata = getattr(chunk, "chunk_metadata", None) or {}
        out["section_title"] = metadata.get("section_title")

        from app.services.lexical_anchor import compute_anchor_fingerprint, normalize

        ids = metadata.get("block_ids") or []
        span = normalize(current_text or "")
        if not ids or not span:
            return out

        # The chunk's own text, back into the blocks it was joined from.
        texts = (chunk.text or "").split("\n\n")
        if len(texts) != len(ids):
            # A single block windowed by tokens because it exceeded the chunk
            # budget: one id, one slice of its text. Anything else is a chunk
            # this code did not build — say nothing rather than guess.
            if len(ids) != 1:
                return out
            texts = [chunk.text or ""]

        for block_id, block_text in zip(ids, texts):
            body = normalize(block_text)
            at = body.find(span)
            if at < 0:
                continue
            out.update(
                anchor_node_key=block_id,
                anchor_offset_start=at,
                anchor_offset_end=at + len(span),
                anchor_fingerprint=compute_anchor_fingerprint(
                    body[:at], span, body[at + len(span):]
                ),
            )
            break
        return out

    @staticmethod
    def _stamp_chunk_keys(submission_id: str, chunk_keys: Dict[str, str], db: Session) -> int:
        """Record, on each chunk row, the context this run graded it under.

        Written here and nowhere else: only a run that reaches persistence may
        advertise a cache. A degraded run computes the same keys and stamps
        none, so the next run keeps comparing against the last verdicts that
        actually exist in the database.
        """
        from app.models.content_chunk import ContentChunk

        stamped = 0
        for row in (
            db.query(ContentChunk)
            .filter(ContentChunk.submission_id == submission_id)
            .all()
        ):
            key = chunk_keys.get(str(row.id))
            if key and row.context_key != key:
                row.context_key = key
                db.add(row)
                stamped += 1
        return stamped

    @staticmethod
    def persist_results(
        submission_id: str,
        violations: List[Dict],
        scores: Dict,
        db: Session,
        analysis_run_id: Optional[str] = None,
        chunk_keys: Optional[Dict[str, str]] = None,
        analysis_warnings: Optional[List[Dict[str, Any]]] = None,
    ) -> ComplianceCheck:
        """
        Persist compliance analysis results to the database.

        `analysis_warnings` are the run's named evidence limitations. They do
        not change the score — that would fabricate a penalty for something
        that was never evaluated — but they do cap the verdict, so a partially
        grounded run can never be stored as a clean "passed".
        """
        try:
            # Create ComplianceCheck
            check = ComplianceCheck(
                submission_id=submission_id,
                overall_score=scores.get("overall", 0.0),
                grade=scores.get("grade", "F"),
                status=ComplianceEngine.cap_status_for_warnings(
                    scores.get("status", "completed"), analysis_warnings
                ),
                scores=scores,
                checked_at=datetime.utcnow()
            )
            db.add(check)
            db.flush()  # Get the ID

            # Persist violations
            ALLOWED_SEV = {"critical", "high", "medium", "low", "moderate", "informational"}
            _rule_version_cache: Dict[str, Optional[int]] = {}
            reused_count = 0

            # The chunks the findings came out of, for their section title and
            # their block ids. Loaded once, and only when something actually
            # names a chunk (document-level findings never do).
            chunk_rows: Dict[str, Any] = {}
            if any(v.get("chunk_id") for v in violations):
                from app.models.content_chunk import ContentChunk

                chunk_rows = {
                    str(row.id): row
                    for row in db.query(ContentChunk)
                    .filter(ContentChunk.submission_id == submission_id)
                    .all()
                }

            for v_data in violations:
                # Carried forward from an unchanged chunk: the row already
                # exists, verdicts and all. Re-parent it and move on.
                if ComplianceEngine._reparent_reused(
                    v_data, check.id, analysis_run_id, db
                ):
                    reused_count += 1
                    continue

                # Normalize severity + category casing at the boundary so the
                # LLM's "CRITICAL" / "Critical" / "critical" all stop forking
                # dashboard aggregations. Falls back to medium / unknown.
                raw_sev = str(v_data.get("severity", "medium")).strip().lower()
                sev = raw_sev if raw_sev in ALLOWED_SEV else "medium"
                # Severity is whitelisted above; category is NOT — it is free
                # text the model chooses (e.g. "claim settlement ratio
                # disclosure & approval"). Unbounded, it overflows
                # violations.category and psycopg2 raises
                # StringDataRightTruncation on the batch INSERT, which discards
                # EVERY violation in the run — the whole analysis is lost after
                # all its LLM spend. Observed in production 2026-07-31: 108
                # findings destroyed by one over-long label. Clamp to the column
                # width so a label can never cost a run again.
                cat = str(v_data.get("category", "unknown")).strip().lower() or "unknown"
                if len(cat) > _CATEGORY_MAX_LEN:
                    logger.warning(
                        "Truncating over-long violation category (%d chars): %r",
                        len(cat), cat,
                    )
                    cat = cat[:_CATEGORY_MAX_LEN].rstrip()

                # Clamp confidence to [0,1]; default 0.85 when LLM doesn't supply.
                try:
                    conf_raw = v_data.get("confidence")
                    confidence = float(conf_raw) if conf_raw is not None else 0.85
                except (TypeError, ValueError):
                    confidence = 0.85
                confidence = max(0.0, min(1.0, confidence))

                # Coerce optional citation fields. cited_precedent_id is a UUID
                # in the rag_compliance_examples table; parse defensively so a
                # bad value from a non-precedent path doesn't break persistence.
                cited_precedent_id = None
                raw_pid = v_data.get("cited_precedent_id")
                if raw_pid:
                    try:
                        import uuid as _uuid
                        cited_precedent_id = _uuid.UUID(str(raw_pid))
                    except (ValueError, TypeError):
                        cited_precedent_id = None

                sim_score = v_data.get("similarity_score")
                try:
                    sim_score = float(sim_score) if sim_score is not None else None
                except (TypeError, ValueError):
                    sim_score = None

                # Which chunk produced this finding. The column has existed since
                # 0001 and was never written — only the positional chunk_index
                # was, which stops identifying anything the moment the document
                # is re-chunked. The reuse cache needs the stable id to find a
                # chunk's previous verdicts. Document-level findings (disclosure)
                # carry no chunk and stay NULL.
                chunk_id = None
                raw_chunk = v_data.get("chunk_id")
                if raw_chunk:
                    try:
                        import uuid as _uuid
                        chunk_id = _uuid.UUID(str(raw_chunk))
                    except (ValueError, TypeError):
                        chunk_id = None

                # Fail-soft by contract: an anchor is a convenience for the
                # editor's highlighter, and no amount of malformed chunk
                # metadata may cost a run its findings.
                try:
                    anchors = ComplianceEngine._anchor_fields(
                        v_data.get("current_text"),
                        chunk_rows.get(str(chunk_id)) if chunk_id else None,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("Anchor computation failed (non-fatal): %s", e)
                    anchors = dict(ComplianceEngine._NO_ANCHOR)

                violation = Violation(
                    compliance_check_id=check.id,
                    analysis_run_id=analysis_run_id,
                    category=cat,
                    severity=sev,
                    description=v_data.get("description", ""),
                    location=v_data.get("location"),
                    current_text=v_data.get("current_text"),
                    suggested_fix=v_data.get("suggested_fix"),
                    auto_fixable=str(v_data.get("auto_fixable", False)).lower(),
                    chunk_id=chunk_id,
                    chunk_index=v_data.get("chunk_index"),
                    confidence=confidence,
                    regulator_quote=v_data.get("regulator_quote"),
                    violation_metadata=v_data.get("violation_metadata"),
                    cited_precedent_id=cited_precedent_id,
                    cited_document_id=v_data.get("cited_document_id"),
                    cited_source_file=v_data.get("cited_source_file"),
                    cited_anchor_text=v_data.get("cited_anchor_text"),
                    cited_comment_verbatim=v_data.get("cited_comment_verbatim"),
                    cited_final_text=v_data.get("cited_final_text"),
                    similarity_score=sim_score,
                    # Citation locators (rule path) — exact clause/page/version.
                    cited_section=v_data.get("cited_section"),
                    cited_page=v_data.get("cited_page"),
                    cited_regulation_version=v_data.get("cited_regulation_version"),
                    # Sub-floor / uncertain findings persisted but kept out of the
                    # score and routed to human review.
                    suppressed=bool(v_data.get("suppressed", False)),
                    suppressed_reason=v_data.get("suppressed_reason"),
                    **anchors,
                )

                # Try to resolve rule_id as UUID + snapshot the rule's version so
                # a later rule edit/deactivation can't rewrite this decision.
                rule_id = v_data.get("rule_id")
                if rule_id:
                    try:
                        import uuid
                        rid = uuid.UUID(str(rule_id))
                        violation.rule_id = rid
                        key = str(rid)
                        if key not in _rule_version_cache:
                            from app.models.rule import Rule
                            _rule_version_cache[key] = (
                                db.query(Rule.version).filter(Rule.id == rid).scalar()
                            )
                        violation.rule_version = _rule_version_cache[key]
                    except (ValueError, TypeError):
                        pass

                db.add(violation)

            # Same transaction as the findings: a chunk may only advertise a
            # reusable verdict if that verdict is committed alongside it.
            if chunk_keys:
                ComplianceEngine._stamp_chunk_keys(submission_id, chunk_keys, db)

            # Flip submission status in the SAME transaction as the check +
            # violations so the three commit atomically (no torn record).
            submission = (
                db.query(Submission).filter(Submission.id == submission_id).first()
            )
            if submission is not None:
                submission.status = "analyzed"
                db.add(submission)

            db.commit()
            db.refresh(check)

            # The findings only exist as of that commit. The render job anchors
            # too, but it runs at upload time with nothing to anchor, so without
            # this pass every violation keeps a NULL anchor_page/anchor_bbox and
            # the reviewer's page view draws no boxes at all. Never fatal: the
            # anchor service swallows its own failures and returns 0.
            if submission is not None:
                from app.services.submission_render_service import anchor_now

                anchor_now(db, submission)

            logger.info(
                f"Persisted compliance check {check.id} with "
                f"{len(violations)} violations ({reused_count} carried forward from "
                f"unchanged chunks), score={scores.get('overall')} grade={scores.get('grade')}"
            )
            return check

        except Exception as e:
            logger.error(f"Failed to persist compliance results: {e}")
            db.rollback()
            raise

    @staticmethod
    async def get_check_summary(check_id: str, db: Session) -> Optional[Dict]:
        """Retrieve a summary of a compliance check."""
        check = db.query(ComplianceCheck).filter(ComplianceCheck.id == check_id).first()
        if not check:
            return None

        violations = db.query(Violation).filter(Violation.compliance_check_id == check.id).all()
        feedback_map = latest_feedback_map(db, [v.id for v in violations])
        counts = finding_counts(violations)

        return {
            "id": str(check.id),
            "submission_id": str(check.submission_id),
            "overall_score": check.overall_score,
            "grade": check.grade,
            "status": check.status,
            "scores": check.scores,
            "checked_at": check.checked_at.isoformat() if check.checked_at else None,
            "violations": [
                serialize_violation(v, feedback_map.get(str(v.id))) for v in violations
            ],
            "violation_count": counts["scored"],
            "suppressed_count": counts["suppressed"],
            "reviewer_added_count": counts["reviewer_added"],
            "finding_count": counts["total"],
            "finding_counts": counts,
        }
