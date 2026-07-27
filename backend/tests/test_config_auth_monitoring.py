"""Phase 1 (audit-trail): auth + cost-model config surface exists with safe defaults."""
from app.config import settings


def test_auth_flags_present_with_defaults():
    assert settings.auth_enabled is True
    # Decision D1: strict, admin-updatable IP binding.
    assert settings.auth_ip_binding_mode == "strict"
    assert settings.session_absolute_ttl_seconds == 8 * 3600
    assert settings.session_idle_ttl_seconds == 60 * 60
    assert settings.login_max_attempts == 5
    assert settings.login_lockout_seconds == 900
    assert settings.session_cookie_secure is True


def test_super_admin_bootstrap_defaults_empty():
    # never hard-code a bootstrap credential; the seed script reads these from env.
    assert settings.super_admin_username == ""
    assert settings.super_admin_password == ""
    assert settings.super_admin_ip == ""


def test_cost_model_config_present():
    assert isinstance(settings.llm_prices, dict)
    assert settings.llm_price_currency == "USD"
