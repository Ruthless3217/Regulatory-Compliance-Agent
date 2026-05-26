import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.vector_projection import (
    _parse_pgvector_literal,
    _cache_key,
    project_2d,
    project_2d_with_method,
)


def test_parse_pgvector_literal():
    assert _parse_pgvector_literal("[0.1,0.2,0.3]") == [0.1, 0.2, 0.3]
    assert _parse_pgvector_literal("[]") == []


def test_cache_key_signature():
    k = _cache_key("umap", n_examples=10, n_rules=5, n_source_docs=3, cap=2000)
    assert k == "umap:10:5:3:2000"


def test_project_2d_returns_xy_per_row():
    vecs = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1], [1, 1, 0, 0], [0, 0, 1, 1]]
    pts = project_2d(vecs, method="pca")
    assert len(pts) == 6
    assert all(len(p) == 2 for p in pts)


def test_project_2d_empty():
    assert project_2d([], method="umap") == []


# ---------------------------------------------------------------------------
# Finding #11 — project_2d_with_method reports the method actually used
# ---------------------------------------------------------------------------

def test_project_2d_with_method_pca_returns_pca():
    """Forcing method='pca' must report 'pca' as the used method."""
    vecs = [[1, 0, 0, 0]] * 5
    points, used = project_2d_with_method(vecs, method="pca")
    assert used == "pca", f"Expected 'pca', got {used!r}"
    assert len(points) == 5
    assert all(len(p) == 2 for p in points)


def test_project_2d_with_method_umap_returns_valid_method():
    """When requesting 'umap' the returned method must be 'umap' or 'pca' (never
    anything else), and coordinates must still be produced."""
    vecs = [[1, 0, 0, 0]] * 5
    points, used = project_2d_with_method(vecs, method="umap")
    assert used in {"umap", "pca"}, f"Unexpected method label: {used!r}"
    assert len(points) == 5
    assert all(len(p) == 2 for p in points)


def test_project_2d_with_method_empty_returns_pca():
    """Empty input must return an empty list and report 'pca' (the safe default)."""
    points, used = project_2d_with_method([], method="umap")
    assert points == []
    assert used == "pca"


def test_project_2d_with_method_too_few_for_umap_falls_back():
    """Fewer than 4 vectors cannot run UMAP; must fall back to PCA."""
    vecs = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]  # n=3 < 4
    points, used = project_2d_with_method(vecs, method="umap")
    assert used == "pca", f"n<4 must fall back to pca, got {used!r}"
    assert len(points) == 3


def test_project_2d_unchanged_by_new_helper():
    """project_2d must still return the same coordinates as project_2d_with_method."""
    vecs = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    pts_old = project_2d(vecs, method="pca")
    pts_new, _ = project_2d_with_method(vecs, method="pca")
    assert pts_old == pts_new
