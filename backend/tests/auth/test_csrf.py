"""CSRF Origin-check middleware — flag-gated cross-origin rejection."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
from fastapi import FastAPI

from app.config import settings
from app.auth.csrf import CsrfOriginMiddleware

try:
    from fastapi.testclient import TestClient
    _HAVE_CLIENT = True
except Exception:
    _HAVE_CLIENT = False

pytestmark = pytest.mark.skipif(not _HAVE_CLIENT, reason="TestClient/httpx unavailable")


def _app():
    app = FastAPI()
    app.add_middleware(CsrfOriginMiddleware)

    @app.post("/x")
    async def x():
        return {"ok": True}

    return TestClient(app)


def test_blocks_foreign_origin_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "csrf_protect", True)
    monkeypatch.setattr(settings, "api_cors_origins", ["https://app.internal"])
    r = _app().post("/x", headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_allows_known_origin_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "csrf_protect", True)
    monkeypatch.setattr(settings, "api_cors_origins", ["https://app.internal"])
    r = _app().post("/x", headers={"origin": "https://app.internal"})
    assert r.status_code == 200


def test_allows_missing_origin_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "csrf_protect", True)
    monkeypatch.setattr(settings, "api_cors_origins", ["https://app.internal"])
    r = _app().post("/x")  # no Origin (non-browser)
    assert r.status_code == 200


def test_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "csrf_protect", False)
    r = _app().post("/x", headers={"origin": "https://evil.example"})
    assert r.status_code == 200
