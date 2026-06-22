from app.schemas.compliance_schemas import ProductFactFinding, PrecedentCitationsResult


def test_product_fact_finding_minimal():
    f = ProductFactFinding(
        product_index=0,
        reviewer_comment="Remove the 'guaranteed returns' claim — eTouch is non-par term.",
        guardrail_text="Do NOT use 'guaranteed returns' language.",
        finding_kind="banned-claim",
        action_type="remove",
    )
    assert f.severity == "moderate"
    assert f.current_text == ""
    assert f.confidence == 0.85


def test_product_fact_finding_missing_mandatory_has_empty_current_text():
    f = ProductFactFinding(
        product_index=0,
        reviewer_comment="Add the plan UIN 116N198V07 and full regulatory descriptor.",
        guardrail_text="Must state Plan UIN 116N198V07.",
        finding_kind="missing-mandatory",
        action_type="add-disclaimer",
    )
    assert f.finding_kind == "missing-mandatory"


def test_result_has_product_fact_findings_default_empty():
    r = PrecedentCitationsResult()
    assert r.product_fact_findings == []


def test_result_accepts_product_fact_findings():
    r = PrecedentCitationsResult(product_fact_findings=[{
        "product_index": 0,
        "reviewer_comment": "Qualify the return-of-premium claim to the ROP variant.",
        "guardrail_text": "Return of premium applies ONLY to the Life Shield ROP variant.",
        "finding_kind": "unqualified-claim",
        "action_type": "rewrite",
        "current_text": "get all your premiums back",
        "severity": "critical",
    }])
    assert r.product_fact_findings[0].severity == "critical"
