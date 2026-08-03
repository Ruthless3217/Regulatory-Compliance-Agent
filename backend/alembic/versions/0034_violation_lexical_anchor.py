"""violation anchors that survive an edit in the Lexical editor.

A finding is located today by `current_text` plus offsets computed at render
time against the extracted text. Those offsets drift the instant a reviewer
types in the editor. These columns pin a finding to a Lexical node and to
offsets *within* that node, with a fingerprint of the surrounding text
(services/lexical_anchor.py) to relocate the span when Lexical re-keys nodes.

All four nullable, on purpose. Every existing violation has none of them, and
every existing path has to keep working on the current text-offset behaviour.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("violations", sa.Column("anchor_node_key", sa.String(64), nullable=True))
    op.add_column("violations", sa.Column("anchor_offset_start", sa.Integer(), nullable=True))
    op.add_column("violations", sa.Column("anchor_offset_end", sa.Integer(), nullable=True))
    op.add_column("violations", sa.Column("anchor_fingerprint", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("violations", "anchor_fingerprint")
    op.drop_column("violations", "anchor_offset_end")
    op.drop_column("violations", "anchor_offset_start")
    op.drop_column("violations", "anchor_node_key")
