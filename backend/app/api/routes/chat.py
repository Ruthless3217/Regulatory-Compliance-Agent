"""
Chat endpoint — streams compliance-aware Q&A grounded in a submission.

Stateless on server side: the client manages conversation history.
`submission_id` is the persistent anchor that fetches submission text +
active rules + existing violations to build the system prompt.
"""
import json
import logging
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.rate_limit import llm_rate_limit
from app.database import get_db
from app.models.compliance_check import ComplianceCheck
from app.models.rule import Rule
from app.models.submission import Submission
from app.models.violation import Violation
from app.services.llm_service import llm_service
from app.services.rag.retrievers.chat_retriever import (
    ChatContext,
    get_chat_retriever,
)
from app.services.rag.retrievers.source_docs_retriever import (
    get_source_docs_retriever,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


class ChatMessage(BaseModel):
    role: str = Field(..., description="user | assistant")
    content: str


class ChatRequest(BaseModel):
    submission_id: UUID
    message: str
    history: List[ChatMessage] = Field(default_factory=list)


class QuickPromptRequest(BaseModel):
    submission_id: UUID
    violation_id: Optional[UUID] = None
    history: List[ChatMessage] = Field(default_factory=list)


def _fallback_rules_block(db: Session) -> str:
    """Used only when RAG is degraded — keeps chat working with a flat top-50."""
    rules = db.query(Rule).filter(Rule.is_active.is_(True)).limit(50).all()
    return "\n".join(
        f"- [{r.category}/{r.severity}] {r.rule_text}" for r in rules
    ) or "  (no active rules in scope)"


def _full_report_block(submission: Submission, db: Session) -> str:
    """Build the full report context — score, grade, per-category scores, every
    violation. Always included so the chat can answer "what are all the
    violations?" or "what's my score?" without depending on RAG retrieval.
    """
    check = (
        db.query(ComplianceCheck)
        .filter(ComplianceCheck.submission_id == submission.id)
        .order_by(ComplianceCheck.checked_at.desc())
        .first()
    )
    if not check:
        return "(no compliance check has been completed for this submission yet)"

    violations = (
        db.query(Violation)
        .filter(Violation.compliance_check_id == check.id)
        .order_by(Violation.severity.asc(), Violation.category.asc())
        .all()
    )

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    violations_sorted = sorted(
        violations,
        key=lambda v: (severity_order.get((v.severity or "low").lower(), 4), v.category or ""),
    )

    sev_counts: Dict[str, int] = {}
    cat_counts: Dict[str, int] = {}
    for v in violations_sorted:
        sev_counts[(v.severity or "?").lower()] = sev_counts.get((v.severity or "?").lower(), 0) + 1
        cat_counts[v.category or "?"] = cat_counts.get(v.category or "?", 0) + 1

    per_cat_scores = ""
    if check.scores and isinstance(check.scores, dict):
        non_meta = {
            k: v for k, v in check.scores.items()
            if k not in {"overall", "grade", "status"}
        }
        if non_meta:
            per_cat_scores = "\n".join(
                f"  - {cat}: {score}" for cat, score in non_meta.items()
            )

    lines = [
        f"Overall score: {check.overall_score}  ·  Grade: {check.grade}  ·  Status: {check.status or 'completed'}",
        f"Total violations: {len(violations_sorted)}",
        "Violations by severity: "
        + ", ".join(f"{k}={v}" for k, v in sorted(sev_counts.items())),
        "Violations by category: "
        + ", ".join(f"{k}={v}" for k, v in sorted(cat_counts.items())),
    ]
    if per_cat_scores:
        lines.append("Per-category scores:")
        lines.append(per_cat_scores)

    if not violations_sorted:
        lines.append("\n(No violations found.)")
        return "\n".join(lines)

    lines.append("\nAll violations (full list):")
    for i, v in enumerate(violations_sorted, 1):
        evidence = (v.current_text or "").strip().replace("\n", " ")
        if len(evidence) > 220:
            evidence = evidence[:217] + "…"
        suggestion = (v.suggested_fix or "").strip().replace("\n", " ")
        if len(suggestion) > 220:
            suggestion = suggestion[:217] + "…"
        lines.append(
            f"  #{i} [{(v.severity or '?').upper()}] [{v.category or '?'}] "
            f"(rule_id={v.rule_id})\n"
            f"      description: {v.description}\n"
            f"      evidence: \"{evidence}\"" + (f"\n      suggested_fix: \"{suggestion}\"" if suggestion else "")
        )
    return "\n".join(lines)


def _build_system_prompt(
    submission: Submission,
    ctx: ChatContext,
    db: Session,
) -> str:
    submission_text = (submission.original_content or "(empty submission)")[:8000]
    full_report = _full_report_block(submission, db)

    if ctx.degraded or not ctx.relevant_rules:
        rule_block = _fallback_rules_block(db)
    else:
        rule_block = "\n".join(
            f"- [{r.get('category', '?')}/{r.get('severity', '?')}] "
            f"(rule_id={r.get('id')}) {r.get('rule_text', '')}"
            for r in ctx.relevant_rules
        )

    chunk_block = (
        "\n\n".join(
            f"[chunk #{c.get('chunk_index')}]\n{(c.get('text') or '')[:1500]}"
            for c in ctx.relevant_chunks
        )
        if ctx.relevant_chunks
        else "(no specific chunks retrieved — falling back to the full submission text above)"
    )

    source_block = (
        "\n\n".join(
            f"[{p.get('regulator', '?').upper()} · {p.get('document_title', '?')}"
            f" · p.{p.get('page_number') or '?'}]\n{(p.get('text') or '')[:1200]}"
            for p in ctx.source_passages
        )
        if ctx.source_passages
        else "(no source passages retrieved)"
    )

    violations_block = (
        json.dumps(ctx.linked_violations, indent=2)
        if ctx.linked_violations
        else "[]"
    )

    rag_status = "degraded — answer from general knowledge if needed" if ctx.degraded else "active"

    return (
        "You are a regulatory-compliance assistant for Bajaj Allianz Life "
        "Insurance, narrowly scoped to reviewing ONE specific marketing "
        "submission against IRDAI, SEBI, and Bajaj brand rules.\n"
        "\n"
        "=== CRITICAL ANTI-HALLUCINATION RULES (read carefully) ===\n"
        "1. 'Violations' means ONLY the rows in the FULL ANALYSIS REPORT block. "
        "A 'rule' in the Most-relevant rules block is NOT a violation. The "
        "rule corpus describes what MIGHT be checked; the FULL REPORT lists "
        "what WAS flagged on this submission. Do NOT promote rules into "
        "violations.\n"
        "2. For any count question (\"how many violations\", \"how many critical\", "
        "\"by severity / category\"), use ONLY the numbers in the FULL REPORT. "
        "Do not derive counts from the rules block or from the submission text.\n"
        "3. Quote rule_ids, descriptions, evidence, and suggested fixes "
        "EXACTLY as they appear in the FULL REPORT. Never invent IDs, "
        "regulator codes, case numbers, or section references that are not "
        "in the data provided.\n"
        "4. If the FULL REPORT says \"no compliance check has been completed\", "
        "say exactly that — do not improvise violations from rules.\n"
        "5. If the user asks about something not in the data (e.g. a specific "
        "violation ID you don't see), reply: \"I don't see that in the report \"\n"
        "   instead of guessing.\n"
        "6. Do not abbreviate the regulator names (IRDAI not IRDA, SEBI not SEC, "
        "IRDAI(I) is wrong, UDIN is unrelated — never write these).\n"
        "\n"
        "=== SCOPE — what you MUST answer ===\n"
        "1. Questions about THIS submission's content and its detected "
        "violations as listed in the FULL REPORT.\n"
        "2. Explanation of the retrieved rules / regulator source passages.\n"
        "3. Scoring, severity, categorization for THIS submission's violations.\n"
        "4. Suggested compliant rewrites of specific passages.\n"
        "5. Insurance/financial compliance concepts — only when clearly tied "
        "to this submission or to a rule in the data below.\n"
        "\n"
        "=== OUT OF SCOPE — refuse with the literal line below ===\n"
        "Anything else (geography, history, sports, trivia, coding, jokes, "
        "personal advice, role-play, news, weather, other companies, etc).\n"
        "Refusal text (use exactly, no additions): \"I can only help with "
        "compliance questions about this submission or the IRDAI/SEBI/Bajaj "
        "rules. Ask me about a violation, a rule, or a rewrite suggestion.\"\n"
        "\n"
        "=== STYLE ===\n"
        "- Be precise. Cite rules by category and severity when relevant.\n"
        "- Quote regulator source passages verbatim only when shown below.\n"
        "- Reference specific evidence text from the submission when "
        "explaining a violation.\n"
        f"(RAG: {rag_status})\n\n"
        f"=== Submission: {submission.title} ===\n"
        f"{submission_text}\n\n"
        "=== FULL ANALYSIS REPORT for this submission (AUTHORITATIVE — "
        "single source of truth for score, grade, violations, counts, "
        "categories, severities, suggested fixes) ===\n"
        f"{full_report}\n\n"
        "=== Most-relevant rules from corpus (NOT violations — possible "
        "checks the analyzer COULD apply; only those marked as detected in "
        "the FULL REPORT are actual violations) ===\n"
        f"{rule_block}\n\n"
        "=== Most-relevant chunks of this submission (retrieved for THIS query) ===\n"
        f"{chunk_block}\n\n"
        "=== Regulator source passages (verbatim from regulator documents) ===\n"
        f"{source_block}\n\n"
        "=== Violations linked to the retrieved rules (subset of FULL REPORT, "
        "matched by rule_id) ===\n"
        f"{violations_block}\n"
        "\n"
        "WHEN ANSWERING:\n"
        "- 'How many violations' / 'list all violations' / 'my score' / "
        "  'critical ones' / 'by severity or category' → answer ONLY from the "
        "FULL ANALYSIS REPORT block above.\n"
        "- 'Explain rule X' or 'what does this regulator say' → use the "
        "retrieved rules and source passages.\n"
        "- 'Rewrite this' / 'suggest a fix' → use the suggested_fix from the "
        "FULL REPORT if it exists; otherwise generate one consistent with the "
        "rule.\n"
        "- If asked about a violation, rule_id, or regulator code that is NOT "
        "in the data above, reply with: \"I don't see that in this report.\"\n"
    )


def _fetch_submission(submission_id: UUID, db: Session) -> Submission:
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    return submission


def _format_sse(event: str, data) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


async def _stream_chat(
    message: str,
    history: List[ChatMessage],
    submission: Submission,
    db: Session,
):
    """Build RAG-augmented system prompt, then stream tokens from the LLM."""
    retriever = get_chat_retriever()
    try:
        ctx = await retriever.retrieve(query=message, submission_id=submission.id, db=db)
    except Exception as e:
        logger.warning(f"chat retriever failed, using degraded context: {e}")
        ctx = ChatContext(degraded=True)

    system_prompt = _build_system_prompt(submission, ctx, db)
    hist = [{"role": h.role, "content": h.content} for h in history]
    # Real token count from the provider's final usage chunk (set via on_usage);
    # `chunks` is only a fallback when the provider omits usage.
    usage = {"tokens": 0}
    chunks = 0
    try:
        async for delta in llm_service.stream_response(
            prompt=message,
            system_prompt=system_prompt,
            history=hist,
            on_usage=lambda t: usage.__setitem__("tokens", t),
        ):
            chunks += 1
            yield _format_sse("token", delta)
        yield _format_sse(
            "done",
            {
                "tokens_used": usage["tokens"] or chunks,
                "model": llm_service.model,
                "rag": {
                    "degraded": ctx.degraded,
                    "rules_retrieved": len(ctx.relevant_rules),
                    "chunks_retrieved": len(ctx.relevant_chunks),
                    "source_passages_retrieved": len(ctx.source_passages),
                    "linked_violations": len(ctx.linked_violations),
                },
            },
        )
    except Exception as e:
        logger.error(f"Chat stream failed: {e}")
        yield _format_sse("error", {"message": str(e)})


def _resolve_violation(violation_id: Optional[UUID], db: Session) -> Violation:
    if not violation_id:
        raise HTTPException(status_code=400, detail="violation_id is required")
    v = db.query(Violation).filter(Violation.id == violation_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Violation not found")
    return v


@router.post("", dependencies=[Depends(llm_rate_limit)])
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    submission = _fetch_submission(req.submission_id, db)
    return StreamingResponse(
        _stream_chat(req.message, req.history, submission, db),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/quote-violation", dependencies=[Depends(llm_rate_limit)])
async def quote_violation(req: QuickPromptRequest, db: Session = Depends(get_db)):
    submission = _fetch_submission(req.submission_id, db)
    target = _resolve_violation(req.violation_id, db)

    # Add the regulator source passage to the message so the LLM has the verbatim quote.
    source_quote = ""
    if target.rule_id:
        passages = get_source_docs_retriever().by_rule(target.rule_id, limit=1)
        if passages:
            p = passages[0]
            source_quote = (
                f"\n\nRegulator source passage [{p['regulator'].upper()} · "
                f"{p['document_title']} · p.{p.get('page_number') or '?'}]:\n"
                f"{(p.get('text') or '')[:1500]}"
            )

    message = (
        "Quote the source rule that flags this passage and explain why it violates compliance. "
        "If a regulator source passage is provided below, quote it verbatim.\n\n"
        f"Category: {target.category}\nSeverity: {target.severity}\n"
        f"Description: {target.description}\nText: {target.current_text or '(n/a)'}"
        f"{source_quote}"
    )
    return StreamingResponse(
        _stream_chat(message, req.history, submission, db),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/suggest-rewrite", dependencies=[Depends(llm_rate_limit)])
async def suggest_rewrite(req: QuickPromptRequest, db: Session = Depends(get_db)):
    submission = _fetch_submission(req.submission_id, db)
    target = _resolve_violation(req.violation_id, db)
    message = (
        "Rewrite this passage to be fully compliant. Preserve marketing intent but "
        "resolve the violation. Return only the rewritten text, no preamble.\n\n"
        f"Original: {target.current_text or '(n/a)'}\n"
        f"Violation: {target.description}"
    )
    return StreamingResponse(
        _stream_chat(message, req.history, submission, db),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
