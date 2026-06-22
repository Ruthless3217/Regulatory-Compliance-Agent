import importlib


def test_critic_profile_inherits_main_but_overrides_model(monkeypatch):
    # main profile config
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("LLM_BASE_URL", "https://res.openai.azure.com")
    monkeypatch.setenv("LLM_API_KEY", "main-key-12345678")
    monkeypatch.setenv("LLM_MODEL", "gpt-5.4")
    monkeypatch.setenv("LLM_AZURE_API_VERSION", "2025-04-01-preview")
    monkeypatch.setenv("LLM_USE_MAX_COMPLETION_TOKENS", "true")
    # critic overrides only the deployment name
    monkeypatch.setenv("CRITIC_LLM_MODEL", "gpt-5.4-nano")

    import app.config as config_mod
    importlib.reload(config_mod)
    fresh_settings = config_mod.Settings()

    import app.services.llm_service as llm_mod
    monkeypatch.setattr(llm_mod, "settings", fresh_settings)

    cfg = llm_mod._resolve_profile("critic")
    assert cfg["provider"] == "azure"          # inherited
    assert cfg["base_url"] == "https://res.openai.azure.com"  # inherited
    assert cfg["model"] == "gpt-5.4-nano"      # overridden
    assert cfg["azure_api_version"] == "2025-04-01-preview"   # inherited
    assert cfg["token_limit_param"] == "max_completion_tokens"  # inherited


def test_critic_api_keys_fall_back_to_main(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "abc,def")
    monkeypatch.setenv("CRITIC_LLM_API_KEY", "")
    import app.config as config
    importlib.reload(config)
    s = config.Settings()
    assert s.critic_llm_api_keys == ["abc", "def"]


def test_critic_inherits_supports_temperature_from_main(monkeypatch):
    """CRITICAL: critic on the same Azure gpt-5.4 family must inherit
    LLM_SUPPORTS_TEMPERATURE=false. Otherwise it sends `temperature` to a
    deployment that rejects it -> 400 -> fail-open -> critic silently no-ops."""
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("LLM_SUPPORTS_TEMPERATURE", "false")
    monkeypatch.setenv("CRITIC_LLM_MODEL", "gpt-5.4-nano")
    # CRITIC_LLM_SUPPORTS_TEMPERATURE intentionally NOT set -> must inherit main.

    import app.config as config_mod
    importlib.reload(config_mod)
    fresh_settings = config_mod.Settings()

    import app.services.llm_service as llm_mod
    monkeypatch.setattr(llm_mod, "settings", fresh_settings)

    cfg = llm_mod._resolve_profile("critic")
    assert cfg["supports_temperature"] is False  # inherited, not the True default


def test_critic_empty_string_bool_env_does_not_crash_and_inherits(monkeypatch):
    """CRITICAL: docker-compose forwards CRITIC_LLM_* bools as ${VAR:-}, which
    materializes as a present-but-empty env var. Pydantic must not crash parsing
    '' as a bool; empty must mean 'inherit main'."""
    monkeypatch.setenv("LLM_SUPPORTS_TEMPERATURE", "false")
    monkeypatch.setenv("LLM_USE_MAX_COMPLETION_TOKENS", "true")
    monkeypatch.setenv("CRITIC_LLM_SUPPORTS_TEMPERATURE", "")
    monkeypatch.setenv("CRITIC_LLM_USE_MAX_COMPLETION_TOKENS", "")

    import app.config as config_mod
    importlib.reload(config_mod)
    fresh_settings = config_mod.Settings()  # must NOT raise ValidationError

    import app.services.llm_service as llm_mod
    monkeypatch.setattr(llm_mod, "settings", fresh_settings)

    cfg = llm_mod._resolve_profile("critic")
    assert cfg["supports_temperature"] is False              # inherited from main
    assert cfg["token_limit_param"] == "max_completion_tokens"  # inherited from main
