import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.vector_projection import _parse_pgvector_literal, _cache_key, project_2d


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
