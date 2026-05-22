"""Pure-unit tests for PineconeStore helpers. No network, no Pinecone SDK calls."""
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from app.services.rag.stores.pinecone_store import (
    _to_pinecone_filter,
    _sanitize_metadata,
)


def test_filter_none_returns_none():
    assert _to_pinecone_filter(None) is None


def test_filter_empty_dict_returns_none():
    assert _to_pinecone_filter({}) is None


def test_filter_single_eq():
    assert _to_pinecone_filter({"category": "irdai"}) == {"category": {"$eq": "irdai"}}


def test_filter_bool_eq():
    assert _to_pinecone_filter({"is_active": True}) == {"is_active": {"$eq": True}}


def test_filter_list_becomes_in():
    out = _to_pinecone_filter({"category": ["irdai", "sebi"]})
    assert out == {"category": {"$in": ["irdai", "sebi"]}}


def test_filter_tuple_becomes_in():
    out = _to_pinecone_filter({"category": ("irdai", "sebi")})
    assert out == {"category": {"$in": ["irdai", "sebi"]}}


def test_filter_combines_multiple_keys():
    out = _to_pinecone_filter({"category": "irdai", "is_active": True})
    assert out == {"category": {"$eq": "irdai"}, "is_active": {"$eq": True}}


def test_filter_uuid_value_coerced_to_str():
    sub_id = uuid.uuid4()
    out = _to_pinecone_filter({"submission_id": sub_id})
    assert out == {"submission_id": {"$eq": str(sub_id)}}


def test_sanitize_drops_none_values():
    out = _sanitize_metadata({"a": "x", "b": None, "c": 1})
    assert out == {"a": "x", "c": 1}


def test_sanitize_coerces_uuid_to_str():
    sub_id = uuid.uuid4()
    out = _sanitize_metadata({"submission_id": sub_id})
    assert out == {"submission_id": str(sub_id)}


def test_sanitize_coerces_uuid_list():
    ids = [uuid.uuid4(), uuid.uuid4()]
    out = _sanitize_metadata({"derived_rule_ids": ids})
    assert out == {"derived_rule_ids": [str(ids[0]), str(ids[1])]}


def test_sanitize_truncates_text_over_32kb():
    long = "x" * 40_000
    out = _sanitize_metadata({"text": long})
    assert len(out["text"]) == 32 * 1024
    assert out["text"] == "x" * (32 * 1024)


def test_sanitize_truncates_rule_text_over_8kb():
    long = "y" * 10_000
    out = _sanitize_metadata({"rule_text": long})
    assert len(out["rule_text"]) == 8 * 1024


def test_sanitize_passes_short_strings_unchanged():
    out = _sanitize_metadata({"text": "hello", "rule_text": "world"})
    assert out == {"text": "hello", "rule_text": "world"}


def test_sanitize_preserves_int_bool_float():
    out = _sanitize_metadata({"chunk_index": 3, "is_active": False, "score": 0.42})
    assert out == {"chunk_index": 3, "is_active": False, "score": 0.42}


def test_sanitize_empty_dict():
    assert _sanitize_metadata({}) == {}
