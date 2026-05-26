"""2-D projection of stored embeddings for the knowledge-base visualization.

A single UMAP (or PCA fallback) fit over ALL selected points stacked into one
matrix, so the three indexes share a comparable space. Cached at module level
keyed on per-table counts (UMAP is too slow to run per page load).
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Dict[str, Any]] = {}

# Tables to project, with the descriptive columns to surface per point.
_SOURCES = [
    ("rag_compliance_examples", "comment_text", ["violation_category", "severity", "reviewer_name"]),
    ("rag_rules", "rule_text", ["category", "severity"]),
    ("rag_source_docs", "text", ["regulator"]),
]


def _parse_pgvector_literal(s: str) -> List[float]:
    s = (s or "").strip()
    if not s or s == "[]":
        return []
    return [float(x) for x in s.strip("[]").split(",") if x.strip()]


def _cache_key(method: str, n_examples: int, n_rules: int, n_source_docs: int, cap: int) -> str:
    return f"{method}:{n_examples}:{n_rules}:{n_source_docs}:{cap}"


def project_2d_with_method(
    vectors: List[List[float]], method: str = "umap"
) -> tuple:
    """Project N D-dim vectors to N 2-D points, returning (points, actual_method).

    Returns the method string that was *actually* used — "umap" only when UMAP
    ran successfully, "pca" in every fallback/empty case.
    """
    if not vectors:
        return [], "pca"
    import numpy as np

    arr = np.array(vectors, dtype="float32")
    n = arr.shape[0]
    use_umap = method == "umap" and n >= 4
    if use_umap:
        try:
            import umap  # type: ignore
            reducer = umap.UMAP(n_components=2, random_state=42, n_neighbors=min(15, n - 1))
            return reducer.fit_transform(arr).tolist(), "umap"
        except Exception as e:
            logger.warning(f"UMAP unavailable/failed ({e}); falling back to PCA")
    # PCA fallback (sklearn).
    from sklearn.decomposition import PCA

    comps = 2 if n >= 2 else 1
    coords = PCA(n_components=comps).fit_transform(arr)
    if comps == 1:
        return [[float(c[0]), 0.0] for c in coords], "pca"
    return coords.tolist(), "pca"


def project_2d(vectors: List[List[float]], method: str = "umap") -> List[List[float]]:
    """Project N D-dim vectors to N 2-D points. PCA fallback if UMAP missing or n<4.

    Existing callers that only need the coordinates should use this function.
    Use :func:`project_2d_with_method` when the *actual* method used is needed.
    """
    points, _ = project_2d_with_method(vectors, method=method)
    return points


def _count(db, table: str) -> int:
    return int(db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)


def compute_projection(method: str = "umap", refresh: bool = False) -> Dict[str, Any]:
    cap = settings.viz_points_per_index
    db = SessionLocal()
    try:
        # ------------------------------------------------------------------ #
        # Collect vectors FIRST so the cache key reflects the number of rows  #
        # that actually have usable (non-empty) embeddings, not raw row counts.#
        # Raw row counts are misleading: if 50 rows exist but all have NULL / #
        # empty embeddings the projection is empty; after re-embedding the row #
        # count stays 50 but the result should be recomputed.  Keying on      #
        # collected-vector counts avoids serving a stale cache in that case.  #
        # (Note: the embedding column is declared NOT NULL in the migration,   #
        # so pgvector never stores a SQL NULL there; however an empty-string / #
        # "[]" value parsed by _parse_pgvector_literal still yields vec=[],    #
        # which the loop below skips — so the scenario IS reachable via        #
        # partially-populated rows written before the embedder runs.)          #
        # ------------------------------------------------------------------ #

        ids: List[str] = []
        index_of: List[str] = []
        labels: List[Optional[str]] = []
        snippets: List[str] = []
        extras: List[Dict[str, Any]] = []
        vectors: List[List[float]] = []
        # per-table collected-vector counts (for the result "counts" dict and cache key)
        collected: Dict[str, int] = {tbl: 0 for tbl, _, _ in _SOURCES}

        for table, snippet_col, extra_cols in _SOURCES:
            cols = ", ".join(["id", "embedding::text"] + [snippet_col] + extra_cols)
            rows = db.execute(
                text(f"SELECT {cols} FROM {table} ORDER BY random() LIMIT :cap"),
                {"cap": cap},
            ).all()
            for r in rows:
                vec = _parse_pgvector_literal(r[1])
                if not vec:
                    continue
                vectors.append(vec)
                ids.append(str(r[0]))
                index_of.append(table)
                snippets.append((r[2] or "")[:160])
                extras.append({c: r[3 + i] for i, c in enumerate(extra_cols)})
                labels.append(extras[-1].get("violation_category") or extras[-1].get("category") or extras[-1].get("regulator"))
                collected[table] += 1

        # Build cache key from collected-vector counts (not raw row counts).
        key = _cache_key(
            method,
            collected.get("rag_compliance_examples", 0),
            collected.get("rag_rules", 0),
            collected.get("rag_source_docs", 0),
            cap,
        )
        if not refresh and key in _CACHE:
            return _CACHE[key]

        # project_2d_with_method returns the method ACTUALLY used (umap or pca),
        # including any silent fallback inside project_2d_with_method.
        coords, used_method = project_2d_with_method(vectors, method=method)
        points = []
        for i, (x, y) in enumerate(coords):
            ex = extras[i]
            points.append(
                {
                    "id": ids[i],
                    "index": index_of[i],
                    "x": round(float(x), 4),
                    "y": round(float(y), 4),
                    "category": ex.get("violation_category") or ex.get("category"),
                    "severity": ex.get("severity"),
                    "reviewer_name": ex.get("reviewer_name"),
                    "label": labels[i],
                    "snippet": snippets[i],
                }
            )

        from datetime import datetime
        result = {
            "method": used_method,  # reflects what was ACTUALLY run, not what was requested
            "computed_at": datetime.utcnow().isoformat(),
            "counts": collected,   # counts of rows with usable embeddings
            "points": points,
        }
        _CACHE[key] = result
        return result
    finally:
        db.close()
