"""Add participant and turn evidence to social sessions.

Revision ID: 20260813_0044
Revises: 20260813_0043
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_0044"
down_revision = "20260813_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("social_session_participants"):
        op.create_table(
            "social_session_participants",
            sa.Column("session_id", sa.Integer(), sa.ForeignKey("social_interaction_sessions.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("resident_id", sa.Integer(), sa.ForeignKey("residents.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("role", sa.String(24), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not inspector.has_table("social_session_turns"):
        op.create_table(
            "social_session_turns",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("session_id", sa.Integer(), sa.ForeignKey("social_interaction_sessions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("turn_index", sa.Integer(), nullable=False),
            sa.Column("actor_resident_id", sa.Integer(), sa.ForeignKey("residents.id", ondelete="SET NULL"), nullable=True),
            sa.Column("turn_type", sa.String(32), nullable=False),
            sa.Column("response", sa.String(32), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("session_id", "turn_index", name="uq_social_session_turn"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_table("social_session_turns"):
        op.drop_table("social_session_turns")
    if inspector.has_table("social_session_participants"):
        op.drop_table("social_session_participants")
