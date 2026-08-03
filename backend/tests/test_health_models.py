"""GET /health/models is a hard security boundary: an explicit allow-list,
never `settings.dict()`. This test pins the response's key SET (not just
"currently no secrets") so a future Settings field addition can't silently
leak through without this test failing.
"""
from fastapi.testclient import TestClient

from app.main import app

ALLOWED_KEYS = {
    "llm_provider",
    "llm_model",
    "critic_llm_model",
    "disclosure_check_enabled",
    "product_grounding_enabled",
}


def test_health_models_keys_are_exactly_the_allow_list():
    client = TestClient(app)
    resp = client.get("/health/models")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == ALLOWED_KEYS


def test_health_models_never_leaks_api_key_fields():
    client = TestClient(app)
    resp = client.get("/health/models")
    body = resp.json()
    for key in body.keys():
        assert "api_key" not in key.lower(), f"leaked secret-shaped key: {key}"
        assert not key.lower().endswith("_key")


def test_health_models_resolves_critic_inherit_fallback(monkeypatch):
    """An empty critic override reports the main model it inherits.

    chat_llm_model is deliberately absent: the chat feature was removed, so the
    payload no longer advertises a model for it. settings.chat_llm_model still
    exists for llm_service's non-analysis profile.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "llm_model", "main-model")
    monkeypatch.setattr(settings, "critic_llm_model", "")

    client = TestClient(app)
    body = client.get("/health/models").json()
    assert body["critic_llm_model"] == "main-model"
