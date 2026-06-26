"""Unit tests for PII redaction applied to the on-disk LLM log."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.redaction import redact_pii


def test_email_redacted():
    out = redact_pii("reach me at john.doe+test@bajajlife.com please")
    assert "john.doe" not in out
    assert "[EMAIL]" in out


def test_pan_redacted():
    out = redact_pii("PAN on file: ABCDE1234F confirmed")
    assert "ABCDE1234F" not in out
    assert "[PAN]" in out


def test_phone_with_country_code_redacted():
    out = redact_pii("Call +91 98765 43210 today")
    assert "98765" not in out
    assert "[PHONE]" in out


def test_bare_ten_digit_phone_redacted():
    out = redact_pii("number 9876543210 end")
    assert "9876543210" not in out
    # bare 10-digit run is caught either as phone or as a long number
    assert "[PHONE]" in out or "[NUM]" in out


def test_long_policy_number_redacted():
    out = redact_pii("policy 100200300400 active")
    assert "100200300400" not in out
    assert "[NUM]" in out


def test_short_numbers_preserved():
    # Scores, severities, small counts must NOT be mangled.
    out = redact_pii("score 95 with 3 critical violations")
    assert out == "score 95 with 3 critical violations"


def test_non_string_passthrough():
    assert redact_pii(None) is None
    assert redact_pii(1234) == 1234


def test_plain_text_unchanged():
    s = "This guarantee claim violates IRDAI rule on assured returns."
    assert redact_pii(s) == s
