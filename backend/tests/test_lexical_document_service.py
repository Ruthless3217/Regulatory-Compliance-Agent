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


# --- can_import: answer the question without doing the work -----------------
#
# GET /submissions/{id} tells the editor whether there is something to seed
# from. Computing the HTML to find out cost seconds of CPU on a long document,
# inside an async handler, which both timed the request out and blocked the
# event loop for every other request on the worker.

def test_can_import_does_not_read_the_file(tmp_path, monkeypatch):
    import io as _io
    from docx import Document as _Document

    path = tmp_path / "big.docx"
    doc = _Document()
    doc.add_paragraph("body")
    buf = _io.BytesIO()
    doc.save(buf)
    path.write_bytes(buf.getvalue())

    opened: list[str] = []
    real_open = open

    def spy(file, *a, **kw):
        opened.append(str(file))
        return real_open(file, *a, **kw)

    monkeypatch.setattr("builtins.open", spy)

    sub = Submission(id=uuid.uuid4(), title="t", content_type="docx", file_path=str(path))
    assert svc.can_import(sub) is True
    assert opened == [], "can_import must not open the upload"


def test_can_import_agrees_with_build_import_html(tmp_path):
    """A yes here that build_import_html then refuses would show the editor an
    empty document with no explanation."""
    import io as _io
    from docx import Document as _Document

    path = tmp_path / "a.docx"
    doc = _Document()
    doc.add_paragraph("body")
    buf = _io.BytesIO()
    doc.save(buf)
    path.write_bytes(buf.getvalue())

    ok = Submission(id=uuid.uuid4(), title="t", content_type="docx", file_path=str(path))
    assert svc.can_import(ok) is True and svc.build_import_html(ok) is not None

    for bad in (
        Submission(id=uuid.uuid4(), title="t", content_type="text", file_path=str(path)),
        Submission(id=uuid.uuid4(), title="t", content_type="docx", file_path=None),
        Submission(id=uuid.uuid4(), title="t", content_type="docx",
                   file_path=str(tmp_path / "absent.docx")),
    ):
        assert svc.can_import(bad) is False
        assert svc.build_import_html(bad) is None
