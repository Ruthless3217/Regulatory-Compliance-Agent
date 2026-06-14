"""Ad-hoc: run a live end-to-end compliance analysis on the synthetic ULIP
brochure using the LOCAL (edited) code against the dockerised Postgres.

Run from the backend/ dir so config picks up backend/.env (Groq + Cohere + the
localhost:5432 docker DB):

    python -m scripts._run_live_analysis
"""
import asyncio
import sys
import uuid

from app.database import SessionLocal
from app.models.submission import Submission
from app.models.compliance_check import ComplianceCheck
from app.models.violation import Violation
from app.models.content_chunk import ContentChunk
from app.services.agents.compliance.engine import ComplianceEngine

DOCX = r"D:\Regulatory-Compliance-Agent\docs\test-fixtures\synthetic_ulip_brochure.docx"


async def main() -> None:
    db = SessionLocal()
    try:
        sub = Submission(
            id=uuid.uuid4(),
            title="LIVE TEST — synthetic ULIP brochure (recall fix)",
            content_type="docx",
            file_path=DOCX,
            status="uploaded",
            product_line="ulip",
        )
        db.add(sub)
        db.commit()
        sid = str(sub.id)
        print(f"Created submission {sid}", flush=True)

        check = await ComplianceEngine.analyze_submission(sid, db)

        # Reload fresh state.
        db.expire_all()
        n_chunks = db.query(ContentChunk).filter(ContentChunk.submission_id == sid).count()
        sub = db.query(Submission).filter(Submission.id == sid).first()
        print(f"\nsubmission.status = {sub.status}")
        print(f"chunks created    = {n_chunks}")

        if check is None:
            print("\n*** analyze_submission returned None (degraded / fail-closed). "
                  "See status above. No gradeable result persisted. ***")
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
