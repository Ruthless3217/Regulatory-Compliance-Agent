import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings


def test_kb_defaults_present():
    assert settings.pgvector_top_k == 15
    assert settings.kb_chunk_size == 500
    assert settings.kb_chunk_overlap == 50
    assert settings.kb_batch_size == 100
    assert settings.kb_min_fuzzy_score == 60
    assert settings.viz_points_per_index == 2000
