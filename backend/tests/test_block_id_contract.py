# backend/tests/test_block_id_contract.py
"""The block-identity contract, asserted from the Python side.

`lexical_anchor.normalize`/`block_id` and `sectionMap.ts`'s `normalize`/`blockId`
must agree byte for byte, and `html_to_blocks` must cut a document into the same
blocks `SectionIdPlugin.readBlocks` does. Both modules say so in their
docstrings; this is what makes saying so true.

The fixture is frozen on purpose. Regenerating it to make a test pass orphans
every anchor already stored against a analysed document.
"""
import json
import pathlib

import pytest

from app.services.lexical_anchor import block_id, block_ids
from app.services.lexical_import import html_to_blocks

CONTRACT = pathlib.Path(__file__).resolve().parents[2] / "contracts" / "block-ids.json"


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_contract_file_is_present_and_filled(contract):
    assert contract["ids"], "fixture has no id cases"
    assert contract["documents"], "fixture has no document cases"
    assert all(case["id"] for case in contract["ids"]), "run the fill script first"


def test_block_id_matches_the_contract(contract):
    for case in contract["ids"]:
        assert block_id(case["text"], case["ordinal"]) == case["id"], case["text"]


def test_html_splits_into_the_contracted_blocks(contract):
    for doc in contract["documents"]:
        texts = [b["text"] for b in html_to_blocks(doc["html"])]
        assert texts == doc["texts"], doc["name"]


def test_document_block_ids_match_the_contract(contract):
    for doc in contract["documents"]:
        assert block_ids(doc["texts"]) == doc["ids"], doc["name"]
