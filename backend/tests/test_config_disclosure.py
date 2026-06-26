from app.config import settings

def test_disclosure_flags_present_with_defaults():
    assert settings.disclosure_check_enabled is True
    assert settings.disclaimers_dir == "data/disclaimers"
    assert settings.disclosure_llm_backstop_enabled is True
