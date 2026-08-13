"""Add local, dynamic physical state for spatial nodes.

Revision ID: 20260813_0041
Revises: 20260813_0040
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_0041"
down_revision = "20260813_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if sa.inspect(conn).has_table("spatial_physical_states"):
        return
    op.create_table(
        "spatial_physical_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("world_key", sa.String(64), nullable=False),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("spatial_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("temperature_c", sa.Float(), nullable=True),
        sa.Column("precipitation", sa.Float(), nullable=False, server_default="0"),
        sa.Column("illumination", sa.Float(), nullable=False, server_default="1"),
        sa.Column("noise_db", sa.Float(), nullable=False, server_default="30"),
        sa.Column("crowd_density", sa.Float(), nullable=False, server_default="0"),
        sa.Column("air_quality", sa.Float(), nullable=False, server_default="100"),
        sa.Column("access_status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("world_key", "node_id", name="uq_spatial_physical_state_world_node"),
    )
    op.create_index("ix_spatial_physical_states_world_node", "spatial_physical_states", ["world_key", "node_id"])


def downgrade() -> None:
    conn = op.get_bind()
    if sa.inspect(conn).has_table("spatial_physical_states"):
        op.drop_table("spatial_physical_states")
