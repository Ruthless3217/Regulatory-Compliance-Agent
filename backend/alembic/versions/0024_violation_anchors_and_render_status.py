"""Violation page/bbox anchors + submission render status.

The document viewer today only substring-matches `violation.location`
("chunk:N", a synthetic string with no real page/bbox) against plain text.
The Compare feature already rasterizes PDF pages and produces positioned word
boxes (`pdf_render_service.PositionedWord`) — this migration gives violations
somewhere to store that same [x0,y0,x1,y1] shape so the review workspace can
reuse it instead of re-deriving anchors from scratch.

`submissions.page_render_status` tracks the async page-rendering pass
(processing|completed|failed|skipped) independently of `submissions.status`
(the analysis lifecycle) — a submission can finish analysis while its pages
are still rendering, or skip rendering entirely for non-paginated content.

All additive/nullable, zero backfill.

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("violations", sa.Column("section_title", sa.Text(), nullable=True))
    op.add_column("violations", sa.Column("anchor_page", sa.Integer(), nullable=True))
    op.add_column("violations", sa.Column("anchor_bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    op.add_column("submissions", sa.Column("page_render_status", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("submissions", "page_render_status")

    op.drop_column("violations", "anchor_bbox")
    op.drop_column("violations", "anchor_page")
    op.drop_column("violations", "section_title")
