from app.config import settings


def test_cross_chunk_context_settings_defaults():
    assert settings.cross_chunk_context_enabled is True
    assert settings.cross_chunk_context_token_budget == 8000


from app.services.preprocessing_service import build_document_context


def _chunks(texts):
    return [{"id": f"c{i}", "chunk_index": i, "text": t} for i, t in enumerate(texts)]


def test_full_mode_includes_every_chunk_and_marks_focal():
    chunks = _chunks(["intro", "claim body", "more body", "Returns not guaranteed. T&C apply."])
    ctx = build_document_context(chunks, focal_index=1, token_budget=8000)
    assert "intro" in ctx
    assert "Returns not guaranteed" in ctx
    assert "[chunk 1]" in ctx
    assert "BEING GRADED" in ctx
    assert "omitted" not in ctx


def test_windowed_mode_always_keeps_footer_even_when_focal_is_early():
    big = "x" * 4000  # ~1000 tokens each by the char/4 estimate
    chunks = _chunks([big] * 11 + ["FOOTER DISCLAIMER: returns not guaranteed"])
    ctx = build_document_context(chunks, focal_index=1, token_budget=2000)
    assert "FOOTER DISCLAIMER" in ctx
    assert "omitted" in ctx
    assert "[chunk 1]" in ctx and "BEING GRADED" in ctx


def test_single_chunk_is_just_the_focal_marker():
    ctx = build_document_context(_chunks(["only section"]), focal_index=0, token_budget=8000)
    assert "[chunk 0]" in ctx
    assert "BEING GRADED" in ctx
    assert "only section" not in ctx


def test_budget_boundary_just_under_stays_full():
    chunks = _chunks(["a" * 2000, "b" * 2000])
    ctx = build_document_context(chunks, focal_index=0, token_budget=1500)
    assert "omitted" not in ctx
    assert "b" * 2000 in ctx


from app.services.preprocessing_service import ContextEngineeringService


def _svc():
    return ContextEngineeringService(db=None)


def test_prompt_without_context_is_unchanged():
    svc = _svc()
    base = svc.create_precedent_prompts("some copy", precedents=[], rules=[])
    assert "DOCUMENT CONTEXT" not in base


def test_prompt_with_context_adds_reference_block_and_instructions():
    svc = _svc()
    ctx = "[chunk 0] intro\n\n[chunk 1] >>> THIS IS THE SECTION BEING GRADED (shown above) <<<\n\n[chunk 2] Returns not guaranteed."
    p = svc.create_precedent_prompts("some copy", precedents=[], rules=[], document_context=ctx)
    assert "DOCUMENT CONTEXT" in p
    assert "Returns not guaranteed." in p
    assert "ONLY from" in p
    assert "do NOT raise it" in p


def test_sweep_prompt_forwards_document_context():
    svc = _svc()
    ctx = "[chunk 2] Returns not guaranteed."
    p = svc.create_completeness_sweep_prompt(
        "some copy", precedents=[], rules=[], already_found=["x"], document_context=ctx
    )
    assert "DOCUMENT CONTEXT" in p
    assert "Returns not guaranteed." in p
    assert "COMPLETENESS SWEEP" in p
