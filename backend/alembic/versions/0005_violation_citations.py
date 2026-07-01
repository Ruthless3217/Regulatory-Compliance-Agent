"""violations — add precedent-citation columns (Phase 1.5).

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
 op.add_column("violations", sa.Column("cited_precedent_id", UUID(as_uuid=True), nullable=True))
 op.add_column("violations", sa.Column("cited_document_id", sa.Text(), nullable=True))
 op.add_column("violations", sa.Column("cited_source_file", sa.Text(), nullable=True))
 op.add_column("violations", sa.Column("cited_anchor_text", sa.Text(), nullable=True))
 op.add_column("violations", sa.Column("cited_comment_verbatim", sa.Text(), nullable=True))
 op.add_column("violations", sa.Column("cited_final_text", sa.Text(), nullable=True))
 op.add_column("violations", sa.Column("similarity_score", sa.Float(), nullable=True))
 op.create_index(
 "ix_violations_cited_precedent_id",
 "violations",
 ["cited_precedent_id"],
 )
 op.create_index(
 "ix_violations_cited_document_id",
 "violations",
 ["cited_document_id"],
 )


def downgrade() -> None:
 op.drop_index("ix_violations_cited_document_id", table_name="violations")
 op.drop_index("ix_violations_cited_precedent_id", table_name="violations")
 op.drop_column("violations", "similarity_score")
 op.drop_column("violations", "cited_final_text")
 op.drop_column("violations", "cited_comment_verbatim")
 op.drop_column("violations", "cited_anchor_text")
 op.drop_column("violations", "cited_source_file")
 op.drop_column("violations", "cited_document_id")
 op.drop_column("violations", "cited_precedent_id")
