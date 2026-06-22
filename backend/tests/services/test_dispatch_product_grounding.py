import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

from app.services.agents.graph import nodes


def test_dispatch_attaches_product_facts_and_passages_on_match():
    state = {
        "chunks": [{"id": "c1", "chunk_index": 0, "text": "Get guaranteed returns 116N198V07", "metadata": {}}],
        "metadata": {"product_match": [{"uin": "116N198V07", "product_name": "eTouch II",
                                        "confidence": 1.0, "method": "uin_regex"}]},
    }
    card = {"uin": "116N198V07", "product_name": "eTouch II"}

    fake_fcs = MagicMock()
    fake_fcs.lookup_many.return_value = [card]
    fake_pdr = MagicMock()
    fake_pdr.retrieve = AsyncMock(return_value=[{"text": "approved wording", "uin": "116N198V07"}])

    with patch("app.services.fact_card_service.get_fact_card_service", return_value=fake_fcs), \
         patch("app.services.rag.retrievers.product_docs_retriever.get_product_docs_retriever",
               return_value=fake_pdr):
        facts, passages = asyncio.run(nodes._resolve_product_grounding(state, state["chunks"]))

    assert facts == [card]
    assert passages["c1"][0]["text"] == "approved wording"


def test_no_match_yields_empty_grounding():
    state = {"chunks": [{"id": "c1", "chunk_index": 0, "text": "x", "metadata": {}}],
             "metadata": {"product_match": []}}
    facts, passages = asyncio.run(nodes._resolve_product_grounding(state, state["chunks"]))
    assert facts == []
    assert passages == {}


def test_grounding_disabled_yields_empty():
    """When product_grounding_enabled=False, return empty grounding regardless of matches."""
    state = {
        "chunks": [{"id": "c1", "chunk_index": 0, "text": "Get guaranteed returns 116N198V07", "metadata": {}}],
        "metadata": {"product_match": [{"uin": "116N198V07", "product_name": "eTouch II",
                                        "confidence": 1.0, "method": "uin_regex"}]},
    }
    with patch("app.config.settings.product_grounding_enabled", False):
        facts, passages = asyncio.run(nodes._resolve_product_grounding(state, state["chunks"]))
    assert facts == []
    assert passages == {}
