"""gotenberg_client — HTTP-transport tests using httpx's own MockTransport
(no new dependency, no network), matching the request shape each Gotenberg
route expects."""
import httpx
import pytest

from app.services import gotenberg_client as gc

_RealClient = httpx.Client  # captured before any test patches gc.httpx.Client


def _client_with(handler):
    """Patch httpx.Client so every call in the module returns a client wired
    to `handler` instead of hitting the network."""
    def _factory(*args, **kwargs):
        return _RealClient(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout"))
    return _factory


def test_convert_to_pdf_posts_the_file_and_returns_bytes(monkeypatch, tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, content=b"%PDF-fake-bytes")

    monkeypatch.setattr(gc.httpx, "Client", _client_with(handler))
    src = tmp_path / "doc.docx"
    src.write_bytes(b"fake docx bytes")

    result = gc.convert_to_pdf(str(src))

    assert result == b"%PDF-fake-bytes"
    assert captured["url"].endswith("/forms/libreoffice/convert")
    assert "multipart/form-data" in captured["content_type"]


def test_convert_to_pdf_raises_on_non_200(monkeypatch, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="LibreOffice crashed")

    monkeypatch.setattr(gc.httpx, "Client", _client_with(handler))
    src = tmp_path / "doc.docx"
    src.write_bytes(b"fake docx bytes")

    with pytest.raises(gc.GotenbergError, match="500"):
        gc.convert_to_pdf(str(src))


def test_convert_to_pdf_raises_on_transport_error(monkeypatch, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(gc.httpx, "Client", _client_with(handler))
    src = tmp_path / "doc.docx"
    src.write_bytes(b"fake docx bytes")

    with pytest.raises(gc.GotenbergError, match="Gotenberg request failed"):
        gc.convert_to_pdf(str(src))


def test_convert_html_to_pdf_posts_index_html(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        return httpx.Response(200, content=b"%PDF-from-html")

    monkeypatch.setattr(gc.httpx, "Client", _client_with(handler))

    result = gc.convert_html_to_pdf("<html><body>Hi</body></html>")

    assert result == b"%PDF-from-html"
    assert captured["url"].endswith("/forms/chromium/convert/html")
    assert b'filename="index.html"' in captured["body"]
    assert b"<html><body>Hi</body></html>" in captured["body"]


def test_convert_html_to_pdf_raises_on_non_200(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad html")

    monkeypatch.setattr(gc.httpx, "Client", _client_with(handler))

    with pytest.raises(gc.GotenbergError, match="400"):
        gc.convert_html_to_pdf("<html></html>")
