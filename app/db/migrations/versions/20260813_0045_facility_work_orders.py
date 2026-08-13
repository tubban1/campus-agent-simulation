"""Add auditable restock and repair work orders.

Revision ID: 20260813_0045
Revises: 20260813_0044
"""

from alembic import op
import sqlalchemy as sa

revision = "20260813_0045"
down_revision = "20260813_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("spatial_facility_work_orders"):
        return
    op.create_table(
        "spatial_facility_work_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        # The established PostgreSQL spatial_resources table predates the
        # fresh-world migration series and has no unique constraint on ``id``.
        # Keep this as an audited application-level identity instead of
        # declaring an invalid foreign key that prevents upgrades.
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("order_type", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("requested_day", sa.Integer(), nullable=False),
        sa.Column("requested_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assigned_resident_id", sa.Integer(), sa.ForeignKey("residents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("completed_by_resident_id", sa.Integer(), sa.ForeignKey("residents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("cost_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_spatial_facility_work_orders_open", "spatial_facility_work_orders", ["resource_id", "status"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("spatial_facility_work_orders"):
        op.drop_table("spatial_facility_work_orders")
