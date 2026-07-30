"""Thin client for the Gotenberg sidecar — converts office documents to PDF via
its LibreOffice route. Synchronous (called from a threadpool background task)."""
import os
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class GotenbergError(RuntimeError):
    """Raised when Gotenberg is unreachable or returns a non-200 response."""


def convert_to_pdf(src_path: str, *, timeout: float = 120.0) -> bytes:
    """POST `src_path` to Gotenberg's LibreOffice route; return the PDF bytes."""
    url = settings.gotenberg_url.rstrip("/") + "/forms/libreoffice/convert"
    filename = os.path.basename(src_path)
    try:
        with httpx.Client(timeout=timeout) as client:
            with open(src_path, "rb") as fh:
                resp = client.post(url, files={"files": (filename, fh)})
    except httpx.HTTPError as e:  # transport/timeout
        raise GotenbergError(f"Gotenberg request failed: {e}") from e
    if resp.status_code != 200:
        raise GotenbergError(f"Gotenberg returned {resp.status_code}: {resp.text[:200]}")
    return resp.content


def convert_html_to_pdf(html: str, *, timeout: float = 120.0) -> bytes:
    """POST an HTML string to Gotenberg's Chromium route; return the PDF bytes.

    The Chromium route resolves relative asset references off the uploaded
    filename, so Gotenberg's own convention is to name it exactly
    `index.html`; we ship no relative assets, so a bare document is fine.
    Not yet called by `submission_export_service` (which reuses
    `pdf_render_service.to_pdf`'s DOCX->PDF seam for all 9 export kinds) —
    kept available for whenever a kind wants native HTML/CSS layout instead
    of a DOCX round-trip, same as `submission_render_service.
    submission_positioned_words` is kept unused until its anchor-pass
    follow-up lands.
    """
    url = settings.gotenberg_url.rstrip("/") + "/forms/chromium/convert/html"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                url, files={"files": ("index.html", html.encode("utf-8"), "text/html")}
            )
    except httpx.HTTPError as e:  # transport/timeout
        raise GotenbergError(f"Gotenberg request failed: {e}") from e
    if resp.status_code != 200:
        raise GotenbergError(f"Gotenberg returned {resp.status_code}: {resp.text[:200]}")
    return resp.content
