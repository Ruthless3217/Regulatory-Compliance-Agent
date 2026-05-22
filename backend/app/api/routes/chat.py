"""
Chat endpoint — streams compliance-aware Q&A grounded in a submission.

Stateless on server side: the client manages conversation history.
`submission_id` is the persistent anchor that fetches submission text +
active rules + existing violations to build the system prompt.
"""
import json
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

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


def _build_system_prompt(
    submission: Submission,
    ctx: ChatContext,
    db: Session,
) -> str:
    submission_text = (submission.original_content or "(empty submission)")[:8000]

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
        "You are a regulatory-compliance assistant for Bajaj Allianz Life Insurance, "
        "reviewing marketing content for adherence to IRDAI, SEBI, and Bajaj brand rules. "
        "Be precise, cite rules by category and severity when relevant, quote regulator "
        "source passages when available, and propose compliant rewrites when asked.\n"
        f"(RAG: {rag_status})\n\n"
        f"=== Submission: {submission.title} ===\n"
        f"{submission_text}\n\n"
        "=== Most-relevant rules (retrieved) ===\n"
        f"{rule_block}\n\n"
        "=== Most-relevant chunks of this submission (retrieved) ===\n"
        f"{chunk_block}\n\n"
        "=== Regulator source passages (retrieved) ===\n"
        f"{source_block}\n\n"
        "=== Linked detected violations (filtered to retrieved rules) ===\n"
        f"{violations_block}\n"
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
    total_tokens = 0
    try:
        async for delta in llm_service.stream_response(
            prompt=message, system_prompt=system_prompt, history=hist
        ):
            total_tokens += 1
            yield _format_sse("token", delta)
        yield _format_sse(
            "done",
            {
                "tokens_used": total_tokens,
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


@router.post("")
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    submission = _fetch_submission(req.submission_id, db)
    return StreamingResponse(
        _stream_chat(req.message, req.history, submission, db),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/quote-violation")
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


@router.post("/suggest-rewrite")
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
