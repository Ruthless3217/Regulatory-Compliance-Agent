"""Read-only post-deploy self-check. Writes NOTHING, ever.

Run it INSIDE the backend container, after `up-shared.sh` reports healthy:

    sudo podman exec compliance-backend python -m scripts.verify_deploy

Prints a PASS/WARN/FAIL table and exits 1 if any check FAILed (WARN is exit 0 —
it flags a degraded-but-serving deploy, e.g. an empty precedent corpus).

Checks:
  alembic     schema is at the migration head shipped in this image
  tables      every table the models + the raw-SQL RAG migrations declare exists
  rows        rules / precedent_cases / submissions counts
  admins      at least one active user whose role grants users:manage
  vector_dim  the vector columns were migrated at the configured RAG_EMBEDDING_DIM
              (the width is baked in by the migration and cannot be re-migrated)
  embeddings  every embedding-bearing table is single-model, and that model is
              the one the running config would query with (a cross-model corpus
              scores plausibly but means nothing — pgvector_store's
              _assert_embedding_compat fails closed on it at query time)
  gotenberg   the DOCX->PDF sidecar answers on settings.gotenberg_url

It runs from inside the container on purpose: gotenberg and shared-postgres are
compose-network DNS names that do not resolve from the host.
"""
import sys
import urllib.request
from pathlib import Path

from sqlalchemy import inspect, text

import app.models  # noqa: F401  — registers every model on Base.metadata
from app.auth.permissions import ROLE_PERMISSIONS, role_has
from app.config import settings
from app.database import Base, engine

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Created by raw SQL in alembic (0002, 0012) so they have no SQLAlchemy model
# and never show up in Base.metadata.
RAW_SQL_TABLES = [
    "rag_chunks", "rag_rules", "rag_source_docs",
    "rag_compliance_examples", "rag_product_docs", "precedent_cases",
]

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name, status, detail):
    results.append((name, status, detail))


def check_alembic(conn):
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    # alembic.ini's script_location is resolved against the CWD, so pin both to
    # backend/ — this must work from anywhere, not just `cd /app`.
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = set(script.get_heads())
    current = set(MigrationContext.configure(conn).get_current_heads())
    if current == heads:
        check("alembic", PASS, f"at head {sorted(heads)}")
    elif not current:
        check("alembic", FAIL, f"database has NO alembic version -- migrations never ran (head is {sorted(heads)})")
    else:
        check("alembic", FAIL, f"at {sorted(current)} but image ships head {sorted(heads)} -- run `alembic upgrade head`")


def check_tables(conn):
    expected = set(Base.metadata.tables) | set(RAW_SQL_TABLES)
    present = set(inspect(conn).get_table_names())
    missing = sorted(expected - present)
    if missing:
        check("tables", FAIL, f"{len(missing)} missing: {', '.join(missing)}")
    else:
        check("tables", PASS, f"all {len(expected)} expected tables present")


def check_rows(conn):
    counts = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
              for t in ("rules", "precedent_cases", "submissions")}
    detail = ", ".join(f"{t}={n}" for t, n in counts.items())
    # No rules = nothing to grade against; seed_rules never ran.
    if not counts["rules"]:
        check("rows", FAIL, f"{detail} -- rules is EMPTY, run `python -m scripts.seed_rules`")
    elif not counts["precedent_cases"]:
        check("rows", WARN, f"{detail} -- no precedent corpus, retrieval is degraded")
    else:
        check("rows", PASS, detail)


def check_admins(conn):
    # Derived from permissions.py, not hardcoded, so a new admin-ish role counts.
    admin_roles = sorted(r for r in ROLE_PERMISSIONS if role_has(r, "users:manage"))
    rows = conn.execute(
        text("SELECT role, count(*) FROM users WHERE is_active AND role = ANY(:roles) GROUP BY role"),
        {"roles": admin_roles},
    ).all()
    if rows:
        check("admins", PASS, ", ".join(f"{r}={n}" for r, n in rows))
    else:
        check("admins", FAIL, f"no active user with any of {admin_roles} -- run `python -m scripts.seed_admin`")


def check_embeddings(conn):
    """Same idea as db_restore.sh's provenance report, but fail-closed.

    Which tables carry vectors is asked of the schema rather than listed here,
    so a new embedding table is covered the day its migration lands.
    """
    tables = [r[0] for r in conn.execute(text(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'embedding_model' "
        "ORDER BY table_name"
    )).all()]
    if not tables:
        check("embeddings", WARN, "no embedding-bearing tables found")
        return

    seen: dict[str, set] = {}
    for t in tables:
        models = {r[0] for r in conn.execute(text(
            f"SELECT DISTINCT embedding_model FROM {t} WHERE embedding_model IS NOT NULL"
        )).all()}
        if models:
            seen[t] = models

    mixed = {t: sorted(m) for t, m in seen.items() if len(m) > 1}
    if mixed:
        check("embeddings", FAIL, "MIXED-MODEL corpus -- " + "; ".join(f"{t}: {m}" for t, m in mixed.items()))
        return

    stored = {m for models in seen.values() for m in models}
    if not stored:
        check("embeddings", WARN, "no stamped vectors (empty or pre-0009 corpus)")
        return
    active, source = _expected_embedding_identity()
    off = sorted(stored - {active})
    if off:
        check("embeddings", FAIL,
              f"corpus embedded with {off} but {source}={active} -- retrieval fails closed")
    else:
        check("embeddings", PASS, f"single-model {active} across {len(seen)} table(s)")


def _expected_embedding_identity() -> tuple:
    """(expected model stamp, the env var that determines it) — resolved the SAME
    way the runtime guard resolves it, which is NOT `RAG_EMBEDDING_MODEL`.

    pgvector_store stamps `embedding_model` from the live embedder's `.model`
    attribute, and for the only supported provider (azure_cohere) that is the
    Foundry DEPLOYMENT name:

        AzureCohereEmbedder.__init__:
            self.model = deployment or settings.azure_cohere_embed_deployment

    `settings.rag_embedding_model` drives nothing in the retrieval path — grep
    shows exactly two uses: its own default in config.py, and a `kb_version`
    label written onto rule_feedback rows. Comparing against it produced a FALSE
    FAILURE on a correctly-configured deployment whose corpus and embedder
    agreed, which is worse than no check at all: it invites someone to "fix" a
    healthy corpus by re-embedding 2,440 precedents.

    Resolved from settings rather than by constructing the embedder, so this
    preflight never needs Azure credentials or a network round-trip.
    """
    provider = (settings.rag_embedding_provider or "").strip().lower()
    if provider == "azure_cohere":
        return settings.azure_cohere_embed_deployment, "AZURE_COHERE_EMBED_DEPLOYMENT"
    # No other provider is wired into the factory today; fall back to the
    # generic label and say which var was used so a mismatch is debuggable.
    return settings.rag_embedding_model, "RAG_EMBEDDING_MODEL"


def check_vector_dim(conn):
    """The vector column WIDTH is frozen when the migration runs.

    0002 and 0012 do `VECTOR({int(os.getenv("RAG_EMBEDDING_DIM", "1536"))})`, so a
    migration run without that env var (a host shell, a one-off migration job)
    bakes 1536 into a deployment that embeds at 1024. Nothing complains until the
    first embedding write, and re-running migrations cannot fix it — the column
    type is already set and needs an ALTER or a rebuild.
    """
    rows = conn.execute(text(
        "SELECT c.relname, a.atttypmod FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND a.attname = 'embedding' "
        "AND NOT a.attisdropped AND a.attnum > 0 ORDER BY c.relname"
    )).all()
    if not rows:
        check("vector_dim", WARN, "no vector columns found")
        return
    want = settings.rag_embedding_dim
    wrong = [(t, d) for t, d in rows if d != want]
    if wrong:
        names = [t for t, _ in wrong]
        shown = ", ".join(names[:3]) + (f", +{len(names) - 3}" if len(names) > 3 else "")
        check("vector_dim", FAIL,
              f"RAG_EMBEDDING_DIM={want} but {len(wrong)} column(s) are "
              f"{sorted({f'vector({d})' for _, d in wrong})} ({shown}) -- migrated with the "
              f"wrong dim; needs ALTER/rebuild, re-running alembic will NOT fix it")
    else:
        check("vector_dim", PASS, f"all {len(rows)} vector column(s) are vector({want})")


def check_gotenberg():
    url = settings.gotenberg_url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            code = resp.status
        check("gotenberg", PASS if code == 200 else FAIL, f"{url} -> HTTP {code}")
    except Exception as exc:
        check("gotenberg", FAIL, f"{url} unreachable ({type(exc).__name__}) -- DOCX/PDF export will fail")


def main() -> int:
    try:
        engine.connect().close()
    except Exception as exc:
        check("database", FAIL, f"cannot connect: {type(exc).__name__}: {exc}")
    else:
        # One connection per check: a failing query aborts its transaction, and
        # sharing one would cascade "InFailedSqlTransaction" onto every check after it.
        for fn in (check_alembic, check_tables, check_rows, check_admins,
                   check_vector_dim, check_embeddings):
            name = fn.__name__[len("check_"):]
            try:
                with engine.connect() as conn:
                    fn(conn)
            except Exception as exc:
                check(name, FAIL, f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
    check_gotenberg()

    width = max(len(n) for n, _, _ in results)
    print()
    print(f"{'CHECK'.ljust(width)}  STATUS  DETAIL")
    print(f"{'-' * width}  ------  {'-' * 40}")
    for name, status, detail in results:
        print(f"{name.ljust(width)}  {status:<6}  {detail}")
    failed = [n for n, s, _ in results if s == FAIL]
    print()
    print(f"FAILED: {', '.join(failed)}" if failed else "All checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
