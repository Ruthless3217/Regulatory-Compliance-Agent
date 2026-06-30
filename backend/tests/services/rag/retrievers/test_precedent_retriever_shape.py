import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))

from app.services.rag.ports import SearchHit
from app.services.rag.retrievers.precedent_retriever import _hit_to_precedent


def test_hit_to_precedent_maps_fields_and_score():
    hit = SearchHit(
        id="abc",
        score=0.73,
        fields={
            "reviewer_role": "Ayushi Sharma/Pune HO/Legal Compliance and FPU/Life",
            "reviewer_comment": "Add disclaimer",
            "span_context": "draft chunk",
            "after_text": "final",
            "issue_type": "disclaimer issue",
            "severity": "critical",
            "ticket": "123",
            "highlighted_span": "anchor",
        },
    )
    p = _hit_to_precedent(hit)
    assert p["id"] == "abc"
    assert p["score"] == 0.73
    assert p["reviewer_name"].endswith("/Life")
    assert p["comment_text"] == "Add disclaimer"
    assert p["chunk_text"] == "draft chunk"
    assert p["final_text_chunk"] == "final"
    assert p["violation_category"] == "disclaimer issue"
    assert p["severity"] == "critical"
    assert p["document_id"] == "123"
