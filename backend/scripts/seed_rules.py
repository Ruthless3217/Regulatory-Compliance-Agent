"""Seed compliance rules from YAML.

Usage (after `alembic upgrade head`):
    python -m scripts.seed_rules

Idempotent by category, rule text, and product scope. When scope changes, an
active legacy row is superseded so historical findings keep their provenance.
"""
import os
import sys
from datetime import datetime, timezone
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


def _scoped_successor(existing: Rule, product_line: str) -> Rule:
    """Create an audit-preserving scope correction for an active seed rule."""
    return Rule(
        category=existing.category,
        rule_text=existing.rule_text,
        severity=existing.severity,
        keywords=existing.keywords,
        pattern=existing.pattern,
        is_active=True,
        rule_metadata=existing.rule_metadata,
        version=(existing.version or 1) + 1,
        effective_date=datetime.now(timezone.utc),
        product_line=product_line,
        jurisdiction=existing.jurisdiction,
        points_deduction=existing.points_deduction,
        created_by=existing.created_by,
        reliability_alpha=existing.reliability_alpha,
        reliability_beta=existing.reliability_beta,
        is_auto_generated=existing.is_auto_generated,
        generated_from_industry=existing.generated_from_industry,
        generation_source=existing.generation_source,
        confidence_score=existing.confidence_score,
    )


def seed_file(db, path: Path) -> tuple[int, int]:
    data = load_yaml(path)
    category = data["category"]
    source = data.get("generation_source", f"seed/{path.stem}")
    confidence = float(data.get("confidence_score", 0.9))
    rules = data.get("rules", [])

    existing_rows = db.query(Rule).filter(
        Rule.category == category,
        Rule.superseded_by.is_(None),
    ).all()
    existing_by_text: dict[str, list[Rule]] = {}
    for existing in existing_rows:
        existing_by_text.setdefault(existing.rule_text, []).append(existing)

    desired_scopes: dict[str, set[str]] = {}
    for seed_rule in rules:
        desired_scopes.setdefault(seed_rule["rule_text"], set()).add(
            seed_rule["product_line"]
        )

    inserted = 0
    versioned = 0
    for r in rules:
        text = r["rule_text"]
        product_line = r["product_line"]
        candidates = existing_by_text.setdefault(text, [])
        if any(existing.product_line == product_line for existing in candidates):
            continue

        # Re-running seeds upgrades one active legacy or obsolete-scope row
        # without rewriting the version already cited by historical findings.
        obsolete = next(
            (
                existing
                for existing in candidates
                if existing.is_active
                and existing.product_line not in desired_scopes[text]
            ),
            None,
        )
        if obsolete is not None:
            successor = _scoped_successor(obsolete, product_line)
            db.add(successor)
            db.flush()
            obsolete.is_active = False
            obsolete.superseded_by = successor.id
            candidates.append(successor)
            versioned += 1
            continue

        new_rule = Rule(
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
        db.add(new_rule)
        candidates.append(new_rule)
        inserted += 1

    db.commit()
    return inserted, versioned


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
        total_versioned = 0
        for yml in sorted(SEEDS_DIR.glob("*.yaml")):
            inserted, versioned = seed_file(db, yml)
            print(
                f"  {yml.name}: inserted {inserted}, "
                f"scope-versioned {versioned} rules"
            )
            total += inserted
            total_versioned += versioned
        print(
            f"Done. Inserted {total} and scope-versioned "
            f"{total_versioned} rules across all seed files."
        )
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
