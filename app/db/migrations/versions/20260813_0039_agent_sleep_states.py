"""Persist physiological night-state labels for residents.

Revision ID: 20260813_0039
Revises: 20260812_0038
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_0039"
down_revision = "20260812_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("agent_body_states"):
        return
    columns = {column["name"] for column in inspector.get_columns("agent_body_states")}
    if "sleep_state" not in columns:
        op.add_column(
            "agent_body_states",
            sa.Column("sleep_state", sa.String(length=32), nullable=False, server_default="awake"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_table("agent_body_states") and "sleep_state" in {
        column["name"] for column in inspector.get_columns("agent_body_states")
    }:
        op.drop_column("agent_body_states", "sleep_state")
