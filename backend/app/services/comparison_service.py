"""
Document comparison service — paragraph extraction and word-level diffing.

No LLM calls; pure text processing (python-docx / pdfplumber for extraction,
stdlib difflib for diffing), so comparisons run synchronously in the API layer.
"""
import logging
import re
from difflib import SequenceMatcher
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
    if file_path:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return split_text_paragraphs(f.read())
    return split_text_paragraphs(pasted_text or "")


def word_diff(old_text: str, new_text: str) -> dict:
    """Word-level diff between two paragraphs assumed to be aligned (same position)."""
    old_words = old_text.split()
    new_words = new_text.split()
    matcher = SequenceMatcher(None, old_words, new_words)
    old_out = []
    new_out = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        changed = tag != "equal"
        for w in old_words[i1:i2]:
            old_out.append({"text": w, "changed": changed})
        for w in new_words[j1:j2]:
            new_out.append({"text": w, "changed": changed})
    return {"type": "replace", "old_words": old_out, "new_words": new_out}


def build_diff(old_paragraphs: List[str], new_paragraphs: List[str]) -> List[dict]:
    """Align two paragraph lists and word-diff replaced pairs. Returns ordered diff blocks."""
    matcher = SequenceMatcher(None, old_paragraphs, new_paragraphs)
    blocks: List[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                blocks.append({
                    "type": "equal",
                    "old_text": old_paragraphs[i1 + k],
                    "new_text": new_paragraphs[j1 + k],
                })
        elif tag == "delete":
            for p in old_paragraphs[i1:i2]:
                blocks.append({"type": "delete", "old_text": p})
        elif tag == "insert":
            for p in new_paragraphs[j1:j2]:
                blocks.append({"type": "insert", "new_text": p})
        elif tag == "replace":
            old_slice = old_paragraphs[i1:i2]
            new_slice = new_paragraphs[j1:j2]
            if len(old_slice) == len(new_slice):
                for op, np in zip(old_slice, new_slice):
                    blocks.append(word_diff(op, np))
            else:
                for p in old_slice:
                    blocks.append({"type": "delete", "old_text": p})
                for p in new_slice:
                    blocks.append({"type": "insert", "new_text": p})
    return blocks
