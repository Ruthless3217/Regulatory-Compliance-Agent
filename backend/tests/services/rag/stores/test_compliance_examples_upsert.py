"""Pure-unit tests for PgVectorStore rag_compliance_examples upsert wiring."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from app.services.rag.ports import IndexName, VectorDoc
from app.services.rag.stores import pgvector_store as pg


def test_index_name_includes_compliance_examples():
    # Literal membership check via the store's dispatch dicts.
    assert "rag_compliance_examples" in pg._UPSERT_SQL
    assert "rag_compliance_examples" in pg._RETURN_COLUMNS
    assert "rag_compliance_examples" in pg._FILTER_WHITELIST


def test_filter_whitelist_fields():
    assert pg._FILTER_WHITELIST["rag_compliance_examples"] == {
        "reviewer_name",
        "violation_category",
        "severity",
        "document_id",
    }


def test_upsert_params_maps_all_columns():
    doc = VectorDoc(
        id="11111111-1111-1111-1111-111111111111",
        embedding=[0.1, 0.2, 0.3],
        fields={
            "document_id": "10699",
            "title": "Untitled",
            "task": "insurance_compliance_rewrite",
            "section_label": None,
            "chunk_text": "draft chunk text",
            "anchor_text": "anchor",
            "reviewer_name": "Shailja Saklani/Pune HO/Legal Compliance and FPU/Life",
            "comment_text": "Add product disclaimer below.",
            "final_text_chunk": "final rewrite",
            "violation_category": "disclaimer issue",
            "severity": "critical",
            "source_file": "10699_request_10699.json",
            "embed_text": "Document chunk: draft chunk text\nCompliance comment: Add product disclaimer below.",
        },
    )
    p = pg._upsert_params("rag_compliance_examples", doc)
    assert p["id"] == doc.id
    assert p["document_id"] == "10699"
    assert p["reviewer_name"].endswith("/Life")
    assert p["violation_category"] == "disclaimer issue"
    assert p["severity"] == "critical"
    assert p["source_file"] == "10699_request_10699.json"
    # embedding serialized to pgvector literal
    assert p["embedding"].startswith("[") and p["embedding"].endswith("]")
    assert p["embed_text"] == "Document chunk: draft chunk text\nCompliance comment: Add product disclaimer below."
    assert p["task"] == "insurance_compliance_rewrite"
    assert p["chunk_text"] == "draft chunk text"
    assert p["comment_text"] == "Add product disclaimer below."
    assert p["anchor_text"] == "anchor"
    assert p["final_text_chunk"] == "final rewrite"
    assert p["title"] == "Untitled"


def test_upsert_params_defaults_missing_optionals():
    doc = VectorDoc(
        id="22222222-2222-2222-2222-222222222222",
        embedding=[0.0],
        fields={
            "document_id": "1",
            "chunk_text": "c",
            "comment_text": "m",
            "source_file": "f.json",
            "embed_text": "e",
        },
    )
    p = pg._upsert_params("rag_compliance_examples", doc)
    assert p["title"] is None
    assert p["anchor_text"] is None
    assert p["final_text_chunk"] is None
    assert p["violation_category"] == "other"
    assert p["severity"] == "informational"
