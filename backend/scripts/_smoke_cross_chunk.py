"""Ad-hoc smoke test: re-run the live compliance analysis on the SAME
"5 Financial Gifts" doc that produced submission 0b74a882 (16 violations,
score 5.67, grade F), now with cross-chunk document context + critic enabled.

Run in the container:
    docker compose exec -T backend python -m scripts._smoke_cross_chunk
"""
import asyncio
import sys
import uuid

from app.config import settings
from app.database import SessionLocal
from app.models.submission import Submission
from app.models.compliance_check import ComplianceCheck
from app.models.violation import Violation
from app.models.content_chunk import ContentChunk
from app.services.agents.compliance.engine import ComplianceEngine

# Exact uploaded copy used by the original submission 0b74a882.
DOCX = "/app/uploads/4b2174f6-07d6-4c6d-86f0-04429cf175a3.docx"


async def main() -> None:
    print(
        f"FLAGS: cross_chunk_context_enabled={settings.cross_chunk_context_enabled} "
        f"budget={settings.cross_chunk_context_token_budget} "
        f"critic_enabled={settings.critic_enabled} model={settings.llm_model}",
        flush=True,
    )
    db = SessionLocal()
    try:
        sub = Submission(
            id=uuid.uuid4(),
            title="SMOKE — 5 Financial Gifts (cross-chunk + critic)",
            content_type="docx",
            file_path=DOCX,
            status="uploaded",
        )
        db.add(sub)
        db.commit()
        sid = str(sub.id)
        print(f"Created submission {sid}", flush=True)

        check = await ComplianceEngine.analyze_submission(sid, db)

        db.expire_all()
        n_chunks = db.query(ContentChunk).filter(ContentChunk.submission_id == sid).count()
        sub = db.query(Submission).filter(Submission.id == sid).first()
        print(f"\nsubmission.status = {sub.status}")
        print(f"chunks created    = {n_chunks}")

        if check is None:
            print("\n*** analyze_submission returned None (degraded / fail-closed). ***")
            return

        check = db.query(ComplianceCheck).filter(ComplianceCheck.id == check.id).first()
        viols = db.query(Violation).filter(Violation.compliance_check_id == check.id).all()
        print(f"\nscore={check.overall_score}  grade={check.grade}  status={check.status}")
        print(f"VIOLATIONS PERSISTED = {len(viols)}  "
              f"(suppressed={sum(1 for v in viols if v.suppressed)})")
        print("-" * 88)
        for i, v in enumerate(sorted(viols, key=lambda v: (v.chunk_index or 0)), 1):
            g = (v.violation_metadata or {}).get("grounding")
            sup = " [SUPPRESSED]" if v.suppressed else ""
            print(f"{i:>2}. ch{v.chunk_index} [{v.severity}/{g}] conf={v.confidence:.2f}{sup}")
            print(f"    text : {(v.current_text or '')[:90]!r}")
            print(f"    note : {(v.description or '')[:120]}")
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
