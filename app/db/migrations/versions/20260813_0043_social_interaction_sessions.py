"""Persist consent-aware, colocated social interaction sessions.

Revision ID: 20260813_0043
Revises: 20260813_0042
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_0043"
down_revision = "20260813_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if sa.inspect(conn).has_table("social_interaction_sessions"):
        return
    op.create_table(
        "social_interaction_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("initiator_resident_id", sa.Integer(), sa.ForeignKey("residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("receiver_resident_id", sa.Integer(), sa.ForeignKey("residents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("spatial_nodes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("interaction_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("receiver_response", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_social_interaction_sessions_participants",
        "social_interaction_sessions",
        ["initiator_resident_id", "receiver_resident_id", "created_at"],
    )


def downgrade() -> None:
    conn = op.get_bind()
    if sa.inspect(conn).has_table("social_interaction_sessions"):
        op.drop_table("social_interaction_sessions")
