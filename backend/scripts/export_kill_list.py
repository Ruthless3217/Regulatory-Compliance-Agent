"""Read-only kill-list exporter for KB + rules cleanup review (audit 2026-06-02).

PURPOSE
    Surface the *noise tail* that the existing cleanup did NOT already remove,
    so a human can review concrete rows + counts before any deletion. This
    script ONLY runs SELECTs — it never writes to the live tables.

    Already handled elsewhere (do not re-target here):
      * Pure-response precedents ("done"/"ok"/"added") — migration 0007 purged
        them at rest and precedent_retriever._is_thin drops them at query time.

    What this surfaces:
      RULES
        - source inventory: every distinct generation_source + metadata source
          with counts, so you can see exactly where auto-gen rules came from.
        - kill candidates: auto-generated rules whose source matches a
          wrong-source pattern (marketing / leaflet / product / test artifact).
      PRECEDENTS (rag_compliance_examples)
        - comment frequency: most-repeated comments (surfaces "What do we mean
          by this?" x69 and friends) so repeated low-value flags are obvious.
        - short comments: comments below --short-len chars (terse queries).
        - question-only comments: short pure-question flags ("source?").
        - corpus health stats: empty final_text_chunk %, severity/category mix.

USAGE
    python -m scripts.export_kill_list                 # writes CSVs + prints digest
    python -m scripts.export_kill_list --out exports/kill_list --short-len 12 --top-n 60

OUTPUT
    A timestamp-free directory of CSVs (default: backend/exports/kill_list/) plus
    a console digest. Re-running overwrites the CSVs in place.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import Any, List, Sequence

# Path bootstrap so this runs via `python -m scripts.export_kill_list` and as a
# bare file from backend/.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

# ---------------------------------------------------------------------------
# Tunable predicates (override via CLI). Kept transparent so the kill-list is
# auditable — nothing here deletes; these only decide what lands in a review CSV.
# ---------------------------------------------------------------------------

# Wrong-source markers for auto-generated rules. A rule whose generation_source
# or rule_metadata->>'source' matches any of these (case-insensitive substring)
# is a kill candidate: it was distilled from marketing / product / test content
# but stored as a binding regulatory rule. Tune freely.
DEFAULT_WRONG_SOURCE_MARKERS: Sequence[str] = (
    "marketing",
    "leaflet",
    "brochure",
    "smart-secure",
    "smart secure",
    "financial gift",
    "17641",
    "e2e",
    "re-verify",
    "rule-gen",
    "test",
    "sample",
    "draft",
    "product",
)

# Response denylist already removed by 0007 — excluded here so the precedent
# buckets don't re-report rows that are gone / handled.
RESPONSE_DENYLIST = (
    "done", "ok", "okay", "yes", "no",
    "noted", "agreed", "agree", "fine", "accepted", "approved", "confirmed",
    "added", "edited", "deleted", "revised", "rephrased", "checked", "check",
)


def _write_csv(path: str, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def _trunc(s: Any, n: int = 160) -> str:
    s = "" if s is None else str(s).replace("\n", " ").replace("\r", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------------
# RULES
# ---------------------------------------------------------------------------

def export_rules(db, out_dir: str, markers: Sequence[str]) -> dict:
    # 1) Full source inventory — every distinct (auto?, generation_source,
    #    metadata source) with counts. This is the ground truth for tuning.
    inv = db.execute(text(
        """
        SELECT
            is_auto_generated,
            COALESCE(generation_source, '(null)')              AS gen_source,
            COALESCE(rule_metadata->>'source', '(null)')       AS meta_source,
            count(*)                                           AS n,
            count(*) FILTER (WHERE is_active)                  AS n_active,
            min(category)                                      AS sample_category,
            min(severity)                                      AS sample_severity,
            min(rule_text)                                     AS sample_rule
        FROM rules
        GROUP BY is_auto_generated, gen_source, meta_source
        ORDER BY is_auto_generated DESC, n DESC
        """
    )).fetchall()

    _write_csv(
        os.path.join(out_dir, "rules_source_inventory.csv"),
        ["is_auto_generated", "generation_source", "metadata_source", "count",
         "active_count", "sample_category", "sample_severity", "sample_rule_text"],
        [(r.is_auto_generated, r.gen_source, r.meta_source, r.n, r.n_active,
          r.sample_category, r.sample_severity, _trunc(r.sample_rule)) for r in inv],
    )

    # 2) Kill candidates — auto-gen rules whose source matches a wrong-source
    #    marker. ILIKE OR across both source columns.
    like_clauses = " OR ".join(
        [f"lower(coalesce(generation_source,'')) LIKE :m{i} "
         f"OR lower(coalesce(rule_metadata->>'source','')) LIKE :m{i}"
         for i in range(len(markers))]
    )
    params = {f"m{i}": f"%{m.lower()}%" for i, m in enumerate(markers)}
    cand = db.execute(text(
        f"""
        SELECT id, category, severity, is_active, is_auto_generated,
               COALESCE(generation_source,'(null)') AS gen_source,
               COALESCE(rule_metadata->>'source','(null)') AS meta_source,
               rule_text
        FROM rules
        WHERE is_auto_generated = true AND ({like_clauses})
        ORDER BY is_active DESC, gen_source
        """
    ), params).fetchall()

    _write_csv(
        os.path.join(out_dir, "rules_kill_candidates.csv"),
        ["id", "category", "severity", "is_active", "generation_source",
         "metadata_source", "rule_text"],
        [(r.id, r.category, r.severity, r.is_active, r.gen_source, r.meta_source,
          _trunc(r.rule_text, 400)) for r in cand],
    )

    totals = db.execute(text(
        "SELECT count(*) total, "
        "count(*) FILTER (WHERE is_auto_generated) auto, "
        "count(*) FILTER (WHERE is_active) active FROM rules"
    )).fetchone()

    return {
        "total": totals.total,
        "auto": totals.auto,
        "active": totals.active,
        "kill_candidates": len(cand),
        "kill_active": sum(1 for r in cand if r.is_active),
        "distinct_sources": len(inv),
    }


# ---------------------------------------------------------------------------
# PRECEDENTS (rag_compliance_examples)
# ---------------------------------------------------------------------------

def export_precedents(db, out_dir: str, short_len: int, top_n: int) -> dict:
    deny_arr = "ARRAY[" + ",".join("'%s'" % t for t in RESPONSE_DENYLIST) + "]"
    norm = "lower(regexp_replace(trim(comment_text), '[.!?]+$', ''))"

    # 1) Comment frequency — most-repeated comments (excluding already-purged
    #    response tokens). Surfaces high-volume low-value flags.
    freq = db.execute(text(
        f"""
        SELECT {norm} AS norm_comment,
               count(*) AS n,
               round(avg(length(trim(comment_text))))::int AS avg_len,
               count(*) FILTER (WHERE final_text_chunk IS NOT NULL
                                  AND length(trim(final_text_chunk)) > 0) AS n_with_fix,
               min(comment_text) AS sample
        FROM rag_compliance_examples
        WHERE {norm} <> ALL({deny_arr})
        GROUP BY norm_comment
        HAVING count(*) > 1
        ORDER BY n DESC
        LIMIT :top_n
        """
    ), {"top_n": top_n}).fetchall()

    _write_csv(
        os.path.join(out_dir, "precedent_comment_frequency.csv"),
        ["normalized_comment", "count", "avg_len", "count_with_fix", "sample"],
        [(_trunc(r.norm_comment, 200), r.n, r.avg_len, r.n_with_fix, _trunc(r.sample, 200))
         for r in freq],
    )

    # 2) Short comments — terse flags below the length threshold (not already a
    #    response token). These are the bulk of the "terse query" tail.
    short = db.execute(text(
        f"""
        SELECT id, reviewer_name, comment_text, violation_category, severity,
               source_file,
               (final_text_chunk IS NOT NULL
                  AND length(trim(final_text_chunk)) > 0) AS has_fix
        FROM rag_compliance_examples
        WHERE length(trim(comment_text)) < :slen
          AND {norm} <> ALL({deny_arr})
        ORDER BY length(trim(comment_text)) ASC
        """
    ), {"slen": short_len}).fetchall()

    _write_csv(
        os.path.join(out_dir, "precedent_short_comments.csv"),
        ["id", "reviewer_name", "comment_text", "violation_category", "severity",
         "source_file", "has_fix"],
        [(r.id, r.reviewer_name, _trunc(r.comment_text, 120), r.violation_category,
          r.severity, r.source_file, r.has_fix) for r in short],
    )

    # 3) Question-only comments — short pure questions ("source?", "what do we
    #    mean by this?"): ends with '?', <= 8 words, not already short-bucketed.
    ques = db.execute(text(
        f"""
        SELECT id, reviewer_name, comment_text, violation_category, severity,
               source_file
        FROM rag_compliance_examples
        WHERE trim(comment_text) LIKE '%?'
          AND array_length(regexp_split_to_array(trim(comment_text), '\\s+'), 1) <= 8
          AND {norm} <> ALL({deny_arr})
        ORDER BY comment_text
        """
    )).fetchall()

    _write_csv(
        os.path.join(out_dir, "precedent_question_only.csv"),
        ["id", "reviewer_name", "comment_text", "violation_category", "severity",
         "source_file"],
        [(r.id, r.reviewer_name, _trunc(r.comment_text, 160), r.violation_category,
          r.severity, r.source_file) for r in ques],
    )

    # 4) Corpus health — overall stats so kill-list size has context.
    stats = db.execute(text(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE final_text_chunk IS NULL
                                  OR length(trim(final_text_chunk)) = 0) AS empty_fix,
               count(DISTINCT violation_category) AS n_categories,
               count(DISTINCT severity) AS n_severities,
               count(DISTINCT reviewer_name) AS n_reviewers,
               count(DISTINCT source_file) AS n_source_files
        FROM rag_compliance_examples
        """
    )).fetchone()

    sev_mix = db.execute(text(
        "SELECT COALESCE(severity,'(null)') s, count(*) n FROM rag_compliance_examples "
        "GROUP BY s ORDER BY n DESC"
    )).fetchall()
    cat_mix = db.execute(text(
        "SELECT COALESCE(violation_category,'(null)') c, count(*) n FROM rag_compliance_examples "
        "GROUP BY c ORDER BY n DESC"
    )).fetchall()

    _write_csv(
        os.path.join(out_dir, "precedent_severity_mix.csv"),
        ["severity", "count"], [(r.s, r.n) for r in sev_mix],
    )
    _write_csv(
        os.path.join(out_dir, "precedent_category_mix.csv"),
        ["violation_category", "count"], [(r.c, r.n) for r in cat_mix],
    )

    return {
        "total": stats.total,
        "empty_fix": stats.empty_fix,
        "empty_fix_pct": round(100 * stats.empty_fix / stats.total, 1) if stats.total else 0,
        "short_comments": len(short),
        "question_only": len(ques),
        "repeated_comments": len(freq),
        "n_categories": stats.n_categories,
        "n_severities": stats.n_severities,
        "n_reviewers": stats.n_reviewers,
        "n_source_files": stats.n_source_files,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only KB+rules kill-list exporter")
    p.add_argument("--out", default="exports/kill_list", help="Output dir (relative to backend/)")
    p.add_argument("--short-len", type=int, default=12, help="Comment length below this = terse")
    p.add_argument("--top-n", type=int, default=60, help="Top-N repeated comments to export")
    args = p.parse_args()

    # Windows consoles default to cp1252, which can't encode the arrows/dashes
    # in the digest below. Force UTF-8 so the summary prints cleanly everywhere.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    out_dir = args.out if os.path.isabs(args.out) else os.path.join(
        os.path.dirname(__file__), "..", args.out)
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    db = SessionLocal()
    try:
        rules = export_rules(db, out_dir, DEFAULT_WRONG_SOURCE_MARKERS)
        prec = export_precedents(db, out_dir, args.short_len, args.top_n)
    finally:
        db.close()

    print("\n" + "=" * 66)
    print("  KILL-LIST EXPORT  (read-only — nothing was modified)")
    print("=" * 66)
    print(f"  Output dir: {out_dir}\n")

    print("  RULES")
    print(f"    total={rules['total']}  auto_generated={rules['auto']}  active={rules['active']}")
    print(f"    distinct sources       : {rules['distinct_sources']}  → rules_source_inventory.csv")
    print(f"    wrong-source candidates: {rules['kill_candidates']} "
          f"({rules['kill_active']} still active)  → rules_kill_candidates.csv")

    print("\n  PRECEDENTS (rag_compliance_examples)")
    print(f"    total={prec['total']}  "
          f"empty_final_text={prec['empty_fix']} ({prec['empty_fix_pct']}%)")
    print(f"    categories={prec['n_categories']}  severities={prec['n_severities']}  "
          f"reviewers={prec['n_reviewers']}  source_files={prec['n_source_files']}")
    print(f"    short comments (<{args.short_len} chars): {prec['short_comments']}  "
          f"→ precedent_short_comments.csv")
    print(f"    question-only flags          : {prec['question_only']}  "
          f"→ precedent_question_only.csv")
    print(f"    repeated comments (top {args.top_n})    : {prec['repeated_comments']}  "
          f"→ precedent_comment_frequency.csv")
    print("\n  Review the CSVs, strike anything that should stay, and we'll turn")
    print("  the survivors into a reversible cleanup (audit-table backed, like 0007).")
    print("=" * 66 + "\n")


if __name__ == "__main__":
    main()
