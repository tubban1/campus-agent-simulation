"""Add operational lifecycle state for spatial facilities.

Revision ID: 20260813_0042
Revises: 20260813_0041
"""
from alembic import op
import sqlalchemy as sa

revision = "20260813_0042"
down_revision = "20260813_0041"
branch_labels = None
depends_on = None

def upgrade() -> None:
    conn = op.get_bind()
    if sa.inspect(conn).has_table("spatial_facility_states"):
        return
    op.create_table(
        "spatial_facility_states",
        sa.Column(
            "resource_id",
            sa.Integer(),
            sa.ForeignKey("spatial_resources.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("open_hour", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("close_hour", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("condition", sa.Float(), nullable=False, server_default="100"),
        sa.Column("maintenance_status", sa.String(32), nullable=False, server_default="operational"),
        sa.Column("inventory_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inventory_capacity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_replenished_day", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

def downgrade() -> None:
    conn = op.get_bind()
    if sa.inspect(conn).has_table("spatial_facility_states"):
        op.drop_table("spatial_facility_states")
