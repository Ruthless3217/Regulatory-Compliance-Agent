#!/usr/bin/env python
"""Portable export / restore of the embedding-bearing KB & precedent tables.

WHY THIS EXISTS
---------------
Embeddings are expensive to produce (Groq / Azure Cohere quota) and slow to
re-ingest. When the Postgres + pgvector instance is *shared* — and another
ingest run wipes, overwrites, or "consumes" the rows — you do NOT want to
re-embed the whole corpus from scratch.

This script snapshots the tables that carry the `embedding` vectors to a
portable, gzip'd JSONL bundle you own, and restores them **verbatim**: the
stored vectors are written back as-is, so restore costs ZERO embedding calls.

Unlike `scripts/db_backup.sh` (a full-DB `pg_dump` whose `COPY` restore collides
on a shared database), restore here is idempotent — `INSERT ... ON CONFLICT (id)
DO UPDATE`. It never duplicates rows and never deletes anyone else's data, so it
is safe to run against a database other people also write to.

The trigger-maintained `search_tsv` (BM25) column is intentionally skipped on
export; the table's BEFORE-INSERT trigger recomputes it on restore, so the
keyword index rebuilds itself.

USAGE
-----
 # snapshot everything to ./kb_snapshot/ (a directory of .jsonl.gz files)
 python -m scripts.kb_embeddings_backup export --out ./kb_snapshot

 # snapshot only precedents + product docs
 python -m scripts.kb_embeddings_backup export --out ./kb_snapshot \
 --tables precedent_cases,rag_product_docs

 # restore everything found in the bundle (idempotent upsert)
 python -m scripts.kb_embeddings_backup restore --in ./kb_snapshot

 # compare row counts: live DB vs. bundle
 python -m scripts.kb_embeddings_backup verify --in ./kb_snapshot

Run from the `backend/` directory (so `app` is importable) or inside the
backend container. Honours the same DATABASE_URL / DB_* settings as the app.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import json
import os
import sys
import uuid as _uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Make `app` importable when invoked as a plain file (python scripts/..py)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text # noqa: E402

from app.database import SessionLocal # noqa: E402

# Tables that carry an `embedding` vector. Order matters on restore only in that
# none of these reference each other via FK, so any order is safe.
EMBEDDING_TABLES: Tuple[str, ...] = (
 "rag_rules",
 "rag_chunks",
 "rag_source_docs",
 "rag_compliance_examples",
 "rag_product_docs",
 "precedent_cases",
)

MANIFEST_NAME = "manifest.json"
BUNDLE_VERSION = 1


# --------------------------------------------------------------- introspection

class ColMeta:
 """Type info for one column, used to drive serialization + casting."""

 __slots__ = ("name", "udt", "is_generated")

 def __init__(self, name: str, udt: str, is_generated: bool):
 self.name = name
 self.udt = udt # pg udt_name, e.g. 'vector', 'jsonb', 'uuid', '_uuid'
 self.is_generated = is_generated

 @property
 def is_vector(self) -> bool:
 return self.udt == "vector"

 @property
 def is_jsonb(self) -> bool:
 return self.udt in ("jsonb", "json")

 @property
 def is_uuid_array(self) -> bool:
 return self.udt == "_uuid"

 @property
 def is_timestamptz(self) -> bool:
 return self.udt in ("timestamptz", "timestamp")

 @property
 def is_tsvector(self) -> bool:
 return self.udt == "tsvector"


def _columns(db, table: str) -> List[ColMeta]:
 """Ordered, restorable columns for `table`.

 Drops trigger/generated columns (`search_tsv`, any GENERATED column): they
 are derived, not source data, and the DB repopulates them on insert.
 """
 rows = db.execute(
 text(
 """
 SELECT column_name, udt_name, is_generated
 FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name = :t
 ORDER BY ordinal_position
 """
 ),
 {"t": table},
 ).all()
 if not rows:
 raise SystemExit(
 f"Table '{table}' not found. Have migrations been applied to this DB?"
 )
 cols: List[ColMeta] = []
 for name, udt, is_generated in rows:
 meta = ColMeta(name, str(udt), str(is_generated).upper() == "ALWAYS")
 if meta.is_generated or meta.is_tsvector:
 continue # derived — let the DB rebuild it
 cols.append(meta)
 return cols


# ----------------------------------------------------------------- serialize

def _json_default(o: Any) -> Any:
 if isinstance(o, _uuid.UUID):
 return str(o)
 if isinstance(o, (_dt.datetime, _dt.date)):
 return o.isoformat()
 if isinstance(o, Decimal):
 return float(o)
 if isinstance(o, (bytes, memoryview)):
 return bytes(o).decode("utf-8", "replace")
 raise TypeError(f"Not JSON-serializable: {type(o)!r}")


def _serialize_value(val: Any, col: ColMeta) -> Any:
 """Turn a DB value into a JSON-friendly form, preserving fidelity.

 The pgvector `embedding` comes back from psycopg2 as its text literal
 ('[0.1,0.2,...]') unless an adapter is registered; we keep that string
 verbatim and re-cast it on restore — no float round-tripping, no re-embed.
 """
 if val is None:
 return None
 if col.is_vector:
 # Could be a str literal or a list, depending on driver/adapter.
 if isinstance(val, str):
 return val
 return list(val) # list[float]
 if col.is_uuid_array:
 return [str(x) for x in val]
 # jsonb already arrives as parsed Python objects; uuid/datetime handled by
 # the json encoder default on dump.
 return val


def _row_to_record(row_map: Dict[str, Any], cols: List[ColMeta]) -> Dict[str, Any]:
 return {c.name: _serialize_value(row_map.get(c.name), c) for c in cols}


# ------------------------------------------------------------------- export

def export(out_dir: Path, tables: Iterable[str], batch: int = 1000) -> None:
 out_dir.mkdir(parents=True, exist_ok=True)
 db = SessionLocal()
 manifest: Dict[str, Any] = {
 "bundle_version": BUNDLE_VERSION,
 "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
 "tables": {},
 }
 try:
 for table in tables:
 cols = _columns(db, table)
 col_names = [c.name for c in cols]
 select_sql = f"SELECT {', '.join(col_names)} FROM {table} ORDER BY id"
 path = out_dir / f"{table}.jsonl.gz"
 n = 0
 with gzip.open(path, "wt", encoding="utf-8") as fh:
 # server-side streaming so a huge corpus doesn't load into RAM
 result = db.execute(text(select_sql)).yield_per(batch)
 for row in result:
 rec = _row_to_record(row._mapping, cols)
 fh.write(json.dumps(rec, default=_json_default, ensure_ascii=False))
 fh.write("\n")
 n += 1
 manifest["tables"][table] = {
 "rows": n,
 "columns": [{"name": c.name, "udt": c.udt} for c in cols],
 "file": path.name,
 }
 print(f" exported {n:>7} rows {table} -> {path.name}")
 finally:
 db.close()

 (out_dir / MANIFEST_NAME).write_text(
 json.dumps(manifest, indent=2), encoding="utf-8"
 )
 total = sum(t["rows"] for t in manifest["tables"].values())
 print(f"\nDone. {total} rows across {len(manifest['tables'])} table(s) -> {out_dir}")
 print(f"Manifest: {out_dir / MANIFEST_NAME}")


# ------------------------------------------------------------------- restore

def _insert_sql(table: str, cols: List[ColMeta]) -> str:
 """Build an idempotent upsert: cast vector/jsonb/uuid[]/ts columns back."""
 names = [c.name for c in cols]
 values: List[str] = []
 for c in cols:
 if c.is_vector:
 values.append(f"CAST(:{c.name} AS VECTOR)")
 elif c.is_jsonb:
 values.append(f"CAST(:{c.name} AS JSONB)")
 elif c.is_uuid_array:
 values.append(f"CAST(:{c.name} AS UUID[])")
 elif c.is_timestamptz:
 values.append(f"CAST(:{c.name} AS TIMESTAMPTZ)")
 else:
 values.append(f":{c.name}")
 updates = [f"{n} = EXCLUDED.{n}" for n in names if n != "id"]
 return (
 f"INSERT INTO {table} ({', '.join(names)}) "
 f"VALUES ({', '.join(values)}) "
 f"ON CONFLICT (id) DO UPDATE SET {', '.join(updates)}"
 )


def _record_to_params(rec: Dict[str, Any], cols: List[ColMeta]) -> Dict[str, Any]:
 params: Dict[str, Any] = {}
 for c in cols:
 v = rec.get(c.name)
 if v is None:
 params[c.name] = None
 elif c.is_vector:
 # accept stored str literal or list
 params[c.name] = v if isinstance(v, str) else "[" + ",".join(
 f"{float(x):.7f}" for x in v
 ) + "]"
 elif c.is_jsonb:
 params[c.name] = json.dumps(v)
 elif c.is_uuid_array:
 validated = [str(_uuid.UUID(str(x))) for x in v]
 params[c.name] = "{" + ",".join(validated) + "}"
 else:
 params[c.name] = v
 return params


def restore(in_dir: Path, tables: Optional[Iterable[str]], batch: int = 500) -> None:
 manifest = _load_manifest(in_dir)
 available = list(manifest["tables"].keys())
 targets = [t for t in (tables or available) if t in available]
 if tables:
 missing = [t for t in tables if t not in available]
 if missing:
 print(f" (skip — not in bundle: {', '.join(missing)})")
 if not targets:
 raise SystemExit("Nothing to restore: no matching tables in the bundle.")

 db = SessionLocal()
 try:
 for table in targets:
 cols = _columns(db, table) # introspect LIVE schema for correct casts
 sql = text(_insert_sql(table, cols))
 path = in_dir / manifest["tables"][table]["file"]
 n = 0
 buf: List[Dict[str, Any]] = []
 with gzip.open(path, "rt", encoding="utf-8") as fh:
 for line in fh:
 line = line.strip()
 if not line:
 continue
 rec = json.loads(line)
 buf.append(_record_to_params(rec, cols))
 if len(buf) >= batch:
 db.execute(sql, buf)
 n += len(buf)
 buf.clear()
 if buf:
 db.execute(sql, buf)
 n += len(buf)
 db.commit()
 print(f" upserted {n:>7} rows -> {table}")
 except Exception:
 db.rollback()
 raise
 finally:
 db.close()
 print("\nRestore complete (idempotent upsert — no rows were deleted).")


# -------------------------------------------------------------------- verify

def verify(in_dir: Path, tables: Optional[Iterable[str]]) -> None:
 manifest = _load_manifest(in_dir)
 available = list(manifest["tables"].keys())
 targets = [t for t in (tables or available) if t in available]
 db = SessionLocal()
 print(f"{'table':<28}{'bundle':>10}{'live db':>10}")
 print("-" * 48)
 try:
 for table in targets:
 bundle_n = manifest["tables"][table]["rows"]
 live_n = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
 flag = "" if live_n >= bundle_n else " <-- live has fewer"
 print(f"{table:<28}{bundle_n:>10}{live_n:>10}{flag}")
 finally:
 db.close()


def _load_manifest(in_dir: Path) -> Dict[str, Any]:
 mpath = in_dir / MANIFEST_NAME
 if not mpath.exists():
 raise SystemExit(f"No {MANIFEST_NAME} in {in_dir} — not a valid bundle.")
 return json.loads(mpath.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------- cli

def _parse_tables(s: Optional[str]) -> Optional[List[str]]:
 if not s:
 return None
 out = [t.strip() for t in s.split(",") if t.strip()]
 bad = [t for t in out if t not in EMBEDDING_TABLES]
 if bad:
 raise SystemExit(
 f"Unknown table(s): {', '.join(bad)}. "
 f"Choose from: {', '.join(EMBEDDING_TABLES)}"
 )
 return out


def main(argv: Optional[List[str]] = None) -> None:
 p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
 sub = p.add_subparsers(dest="cmd", required=True)

 pe = sub.add_parser("export", help="snapshot embedding tables to a bundle dir")
 pe.add_argument("--out", required=True, type=Path, help="output directory")
 pe.add_argument("--tables", help="comma-separated subset (default: all)")

 pr = sub.add_parser("restore", help="idempotent upsert a bundle back into the DB")
 pr.add_argument("--in", dest="in_dir", required=True, type=Path)
 pr.add_argument("--tables", help="comma-separated subset (default: all in bundle)")

 pv = sub.add_parser("verify", help="compare bundle row counts to the live DB")
 pv.add_argument("--in", dest="in_dir", required=True, type=Path)
 pv.add_argument("--tables", help="comma-separated subset (default: all in bundle)")

 args = p.parse_args(argv)
 tables = _parse_tables(getattr(args, "tables", None))

 if args.cmd == "export":
 export(args.out, tables or list(EMBEDDING_TABLES))
 elif args.cmd == "restore":
 restore(args.in_dir, tables)
 elif args.cmd == "verify":
 verify(args.in_dir, tables)


if __name__ == "__main__":
 main()
