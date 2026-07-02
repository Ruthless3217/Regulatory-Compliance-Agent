import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.routes import comparisons


def test_router_prefix_and_routes():
    paths = {r.path for r in comparisons.router.routes}
    assert "/comparisons" in paths
    assert "/comparisons/{comparison_id}" in paths


def test_registered_in_app():
    from app.main import app
    paths = {r.path for r in app.routes}
    assert "/comparisons" in paths


# --- Finding #1: MIME-or-extension content-type detection ---

def test_detect_content_type_recognized_mime_wins():
    assert comparisons._detect_content_type("application/pdf", "report.bin") == "pdf"


def test_detect_content_type_generic_mime_docx_extension():
    assert comparisons._detect_content_type("application/octet-stream", "policy.docx") == "docx"


def test_detect_content_type_generic_mime_pdf_extension():
    assert comparisons._detect_content_type("application/octet-stream", "policy.pdf") == "pdf"


def test_detect_content_type_generic_mime_txt_extension():
    assert comparisons._detect_content_type("application/octet-stream", "notes.txt") == "text"


def test_detect_content_type_generic_mime_no_extension():
    assert comparisons._detect_content_type("application/octet-stream", "noext") == "text"


def test_detect_content_type_empty_mime_and_filename():
    assert comparisons._detect_content_type("", "") == "text"


def test_detect_content_type_docx_extension_case_insensitive():
    assert comparisons._detect_content_type("application/octet-stream", "policy.DOCX") == "docx"


# --- Finding #2: extension sanitization ---

def test_sanitize_ext_normal_extension():
    assert comparisons._sanitize_ext("report.pdf") == "pdf"


def test_sanitize_ext_strips_path_separators():
    assert comparisons._sanitize_ext("x.a/b") == "ab"


def test_sanitize_ext_no_extension_defaults_to_txt():
    assert comparisons._sanitize_ext("noext") == "txt"


def test_sanitize_ext_none_filename_defaults_to_txt():
    assert comparisons._sanitize_ext(None) == "txt"


def test_sanitize_ext_all_non_alnum_defaults_to_txt():
    assert comparisons._sanitize_ext("x./") == "txt"
