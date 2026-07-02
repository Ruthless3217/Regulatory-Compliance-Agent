"""
Document comparison service — paragraph extraction and word-level diffing.

No LLM calls; pure text processing (python-docx / pdfplumber for extraction,
stdlib difflib for diffing), so comparisons run synchronously in the API layer.
"""
import logging
import re
from typing import List, Optional

logger = logging.getLogger(__name__)


def split_text_paragraphs(text: str) -> List[str]:
    """Split raw text into paragraphs on one-or-more blank lines."""
    if not text or not text.strip():
        return []
    blocks = re.split(r"\n\s*\n", text.strip())
    return [b.strip() for b in blocks if b.strip()]


def extract_docx_paragraphs(file_path: str) -> List[str]:
    """Extract paragraph text from a DOCX file, one entry per Word paragraph."""
    from docx import Document
    doc = Document(file_path)
    paragraphs: List[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = getattr(para.style, "name", "") or ""
        if style.startswith("Heading") or style == "Title":
            paragraphs.append(f"## {text}")
        else:
            paragraphs.append(text)
    return paragraphs


def extract_pdf_paragraphs(file_path: str) -> List[str]:
    """Extract paragraph text from a PDF: page text joined, then split on blank lines."""
    import pdfplumber
    pages: List[str] = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                pages.append(page_text)
    return split_text_paragraphs("\n\n".join(pages))


def extract_paragraphs(
    file_path: Optional[str], content_type: str, pasted_text: Optional[str] = None
) -> List[str]:
    """Dispatch extraction by content_type: docx/pdf read from file_path, else split pasted_text."""
    if content_type == "docx":
        if not file_path:
            raise ValueError("docx content_type requires file_path")
        return extract_docx_paragraphs(file_path)
    if content_type == "pdf":
        if not file_path:
            raise ValueError("pdf content_type requires file_path")
        return extract_pdf_paragraphs(file_path)
    return split_text_paragraphs(pasted_text or "")
