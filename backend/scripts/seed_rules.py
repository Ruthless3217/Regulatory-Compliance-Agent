"""Seed compliance rules from YAML.

Usage (after `alembic upgrade head`):
    python -m scripts.seed_rules

Idempotent: skips rules whose `rule_text` already exists for that category.
"""
import os
import sys
from pathlib import Path

import yaml

# Allow running as a module from the backend dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models.rule import Rule  # noqa: E402

SEEDS_DIR = Path(__file__).resolve().parent / "seeds"


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def seed_file(db, path: Path) -> int:
    data = load_yaml(path)
    category = data["category"]
    source = data.get("generation_source", f"seed/{path.stem}")
    confidence = float(data.get("confidence_score", 0.9))
    rules = data.get("rules", [])

    existing_by_text = {
        r.rule_text: r
        for r in db.query(Rule).filter(Rule.category == category).all()
    }

    inserted = 0
    for r in rules:
        text = r["rule_text"]
        product_line = r.get("product_line")  # None = global (applies to all products)
        existing = existing_by_text.get(text)
        if existing is not None:
            # Backfill the scope tag onto already-seeded rows so environments
            # seeded before product-aware retrieval pick it up on re-run.
            if product_line and existing.product_line != product_line:
                existing.product_line = product_line
            continue
        db.add(
            Rule(
                category=category,
                rule_text=text,
                severity=r.get("severity", "medium"),
                keywords=r.get("keywords") or [],
                points_deduction=r.get("points_deduction", -5.0),
                product_line=product_line,
                is_active=True,
                is_auto_generated=False,
                generated_from_industry=category,
                generation_source=source,
                confidence_score=confidence,
            )
        )
        inserted += 1

    db.commit()
    return inserted


try:
    from langsmith import traceable
except ImportError:
    def traceable(*_a, **_kw):
        return lambda f: f


@traceable(run_type="chain", name="Seed Rules")
def main() -> None:
    db = SessionLocal()
    try:
        total = 0
        for yml in sorted(SEEDS_DIR.glob("*.yaml")):
            n = seed_file(db, yml)
            print(f"  {yml.name}: inserted {n} rules")
            total += n
        print(f"Done. Inserted {total} new rules across all seed files.")
    finally:
        db.close()

    # Push newly-seeded rules into rag_rules. Best-effort: if the embedder
    # or vector store is misconfigured, we surface the error but don't
    # roll back the seeded rules — they're already in Postgres and the
    # rag_backfill CLI can be re-run later.
    try:
        import asyncio
        from app.services.rag.indexers.rules_indexer import upsert_all_active_rules

        async def _do():
            db2 = SessionLocal()
            try:
                n = await upsert_all_active_rules(db2)
                print(f"RAG: indexed {n} active rules into rag_rules")
            finally:
                db2.close()

        asyncio.run(_do())
    except Exception as e:
        print(f"RAG backfill skipped (non-fatal): {e}")
        print("Run `python -m scripts.rag_backfill --rules` once the RAG layer is configured.")


if __name__ == "__main__":
    main()
