"""Persist dynamic road state separately from immutable imported topology.

Revision ID: 20260813_0046
Revises: 20260813_0045
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_0046"
down_revision = "20260813_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if sa.inspect(conn).has_table("spatial_edge_physical_states"):
        return
    op.create_table(
        "spatial_edge_physical_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("world_key", sa.String(64), nullable=False),
        sa.Column("edge_id", sa.Integer(), sa.ForeignKey("spatial_edges.id", ondelete="CASCADE"), nullable=False),
        sa.Column("access_status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("travel_factor", sa.Float(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("world_key", "edge_id", name="uq_spatial_edge_physical_state_world_edge"),
    )
    op.create_index("ix_spatial_edge_physical_states_world_edge", "spatial_edge_physical_states", ["world_key", "edge_id"])


def downgrade() -> None:
    conn = op.get_bind()
    if sa.inspect(conn).has_table("spatial_edge_physical_states"):
        op.drop_table("spatial_edge_physical_states")
