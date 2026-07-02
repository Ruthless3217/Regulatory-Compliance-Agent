import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_table_name_and_columns():
    from app.models.document_comparison import DocumentComparison
    assert DocumentComparison.__tablename__ == "document_comparisons"
    columns = {c.name for c in DocumentComparison.__table__.columns}
    assert columns == {
        "id", "title", "old_content_type", "new_content_type",
        "old_file_path", "new_file_path", "old_original_content",
        "new_original_content", "diff_result", "status", "error_message",
        "created_by", "created_at",
    }


def test_registered_on_base_metadata():
    from app.models.document_comparison import DocumentComparison  # noqa: F401
    from app.database import Base
    assert "document_comparisons" in Base.metadata.tables
