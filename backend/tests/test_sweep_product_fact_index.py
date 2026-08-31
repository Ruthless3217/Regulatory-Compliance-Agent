"""A product_index must mean the same product in the prompt and in the parser.

`product_fact_findings` carry a `product_index` into the fact-card list the
prompt rendered as "--- PRODUCT 0 ---", "--- PRODUCT 1 ---", ... The first pass
and the completeness sweep produce findings that `merge_findings` combines, and
the merged set is resolved by `map_findings_to_violations` against ONE list.

Since grounding became chunk-aware the first-pass prompt carries the chunk's
cards, so the sweep must carry the same ones. Built from the document-level
list instead, its PRODUCT 0 is a different product from the parser's PRODUCT 0,
and a sweep finding is silently attributed to the wrong product (or dropped
when the index falls outside the shorter chunk list).
"""
import pytest

from app.services.agents.graph.nodes import map_findings_to_violations
from app.services.preprocessing_service import ContextEngineeringService
from app.schemas.compliance_schemas import (
    PrecedentCitationsResult,
    ProductFactFinding,
)


def _card(uin, name):
    return {
        "uin": uin,
        "product_name": name,
        "regulatory_descriptor": f"descriptor for {name}",
        "structural_flags": {"is_unit_linked": True},
        "compliance_guardrails": {
            "claims_marketing_must_avoid": [f"{name} must not promise assured returns"],
            "claims_marketing_must_support": [],
            "must_state": [f"UIN {uin}"],
        },
    }


CARD_A = _card("116L196V04", "Bajaj Life Fortune Gain II")
CARD_B = _card("116N165V01", "Bajaj Life Saral Jeevan Bima")
CARD_C = _card("116N127V04", "Bajaj Life Elite Assure")

DOCUMENT_FACTS = [CARD_A, CARD_B, CARD_C]
CHUNK_FACTS = [CARD_B]          # this chunk is about B only

CHUNK_TEXT = "Bajaj Life Saral Jeevan Bima guarantees returns of 12% every year."


@pytest.fixture
def service():
    return ContextEngineeringService(db=None)


def _prompt_product_at(prompt, index):
    """The product the prompt labelled '--- PRODUCT {index} ---'."""
    marker = f"--- PRODUCT {index} ---"
    assert marker in prompt, f"prompt has no {marker}"
    tail = prompt.split(marker, 1)[1]
    line = next(l for l in tail.splitlines() if l.startswith("Product: "))
    return line[len("Product: "):].split(" (UIN ")[0].strip()


def _violation_product_at(index, product_facts):
    finding = ProductFactFinding(
        product_index=index,
        guardrail_text=f"{product_facts[0]['product_name']} guardrail",
        finding_kind="banned-claim",
        current_text="guarantees returns of 12% every year",
        reviewer_comment="Assured-return claim.",
        action_type="remove",
    )
    violations = map_findings_to_violations(
        PrecedentCitationsResult(
            citations=[], rule_findings=[], novel_findings=[],
            product_fact_findings=[finding],
        ),
        [], rules=[], product_facts=product_facts,
        chunk_id="chunk-1", chunk_index=0, location="chunk:chunk-1",
    )
    return violations


def test_sweep_prompt_and_parser_must_agree_on_product_zero(service):
    """The invariant. Built from the same list, index 0 is the same product."""
    sweep = service.create_completeness_sweep_prompt(
        CHUNK_TEXT, [], rules=[], already_found=[], product_facts=CHUNK_FACTS,
    )
    violations = _violation_product_at(0, CHUNK_FACTS)

    assert _prompt_product_at(sweep, 0) == CARD_B["product_name"]
    assert violations, "a valid product_index must produce a violation"
    assert CARD_B["product_name"] in str(violations[0])


def test_document_level_sweep_prompt_disagrees_with_the_chunk_parser(service):
    """Why the mismatch is not cosmetic: index 0 names two different products."""
    document_sweep = service.create_completeness_sweep_prompt(
        CHUNK_TEXT, [], rules=[], already_found=[], product_facts=DOCUMENT_FACTS,
    )

    # The sweep would call A "PRODUCT 0"; the parser resolves 0 against the
    # chunk list, which is B. A finding about A becomes a finding about B.
    assert _prompt_product_at(document_sweep, 0) == CARD_A["product_name"]
    assert _prompt_product_at(document_sweep, 0) != _prompt_product_at(
        service.create_completeness_sweep_prompt(
            CHUNK_TEXT, [], rules=[], already_found=[], product_facts=CHUNK_FACTS,
        ),
        0,
    )


def test_an_index_beyond_the_chunk_list_is_dropped_not_misfiled(service):
    """The other half of the mismatch: the finding vanishes instead of landing."""
    assert _violation_product_at(2, DOCUMENT_FACTS)      # valid against 3 cards
    assert _violation_product_at(2, CHUNK_FACTS) == []   # dropped against 1


def test_first_pass_and_sweep_prompts_label_products_identically(service):
    """Both passes must render the same product at the same index."""
    first = service.create_precedent_prompts(
        CHUNK_TEXT, [], rules=[], product_facts=CHUNK_FACTS,
    )
    sweep = service.create_completeness_sweep_prompt(
        CHUNK_TEXT, [], rules=[], already_found=[], product_facts=CHUNK_FACTS,
    )

    assert _prompt_product_at(first, 0) == _prompt_product_at(sweep, 0)


def test_every_per_chunk_prompt_and_the_parser_share_one_fact_list():
    """Regression lock for the call sites themselves.

    The mismatch is invisible to behavioural tests without a full graph run and
    a live LLM: both lists are well-formed, so nothing raises — the finding just
    lands on the wrong product. This asserts the one property that prevents it,
    at the only place it can be checked cheaply.
    """
    import inspect
    import re

    from app.services.agents.graph import nodes

    source = inspect.getsource(nodes.analysis_node)
    # The run-level fingerprint is deliberately document-level; drop it and
    # check only what a chunk's prompt or parser receives.
    per_chunk = re.sub(
        r"run_context_fingerprint\(.*?\)", "", source, flags=re.S
    )
    used = set(re.findall(r"product_facts=(\w+)", per_chunk))

    assert used, "expected product_facts to be passed to the per-chunk calls"
    assert used == {"chunk_facts"}, (
        f"every per-chunk product_facts argument must be chunk_facts, got "
        f"{sorted(used)}"
    )
