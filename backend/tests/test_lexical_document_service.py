"""Import must never mutate the upload, and must degrade rather than fail."""
import uuid

from app.models.submission import Submission
from app.services import lexical_document_service as svc


def test_non_docx_returns_none_rather_than_raising(tmp_path):
    sub = Submission(id=uuid.uuid4(), title="t", content_type="text",
                     original_content="hello", file_path=None)
    assert svc.build_import_html(sub) is None


def test_missing_file_returns_none(tmp_path):
    sub = Submission(id=uuid.uuid4(), title="t", content_type="docx",
                     file_path=str(tmp_path / "absent.docx"))
    assert svc.build_import_html(sub) is None


def test_docx_returns_html_and_leaves_the_file_untouched(tmp_path):
    import io
    from docx import Document

    path = tmp_path / "a.docx"
    doc = Document()
    doc.add_heading("Charges", level=2)
    buf = io.BytesIO()
    doc.save(buf)
    path.write_bytes(buf.getvalue())
    before = path.read_bytes()

    sub = Submission(id=uuid.uuid4(), title="t", content_type="docx",
                     file_path=str(path))
    html = svc.build_import_html(sub)

    assert "<h2>" in html
    assert path.read_bytes() == before, "the upload is immutable"


def test_unconvertible_docx_returns_none(tmp_path):
    """A corrupt upload keeps the submission on extracted text rather than
    breaking GET /submissions/{id}."""
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a docx at all")

    sub = Submission(id=uuid.uuid4(), title="t", content_type="docx",
                     file_path=str(path))
    assert svc.build_import_html(sub) is None
