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
