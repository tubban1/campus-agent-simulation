"""Add a pluggable supplier/procurement layer for closing the input loop.

Revision ID: 20260813_0047
Revises: 20260813_0046
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_0047"
down_revision = "20260813_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("procurement_suppliers"):
        op.create_table(
            "procurement_suppliers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("supplier_key", sa.String(96), nullable=False, unique=True),
            sa.Column("supplier_actor_key", sa.String(128), nullable=False),
            sa.Column("upstream_actor_key", sa.String(128), nullable=True),
            sa.Column("item_id", sa.Integer(), nullable=False),
            sa.Column("supply_kind", sa.String(24), nullable=False, server_default="in_world"),
            sa.Column("unit_cost_minor", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("replenish_threshold", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("replenish_qty", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(24), nullable=False, server_default="active"),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_procurement_suppliers_item",
            "procurement_suppliers",
            ["item_id", "status"],
        )
    if not inspector.has_table("procurement_orders"):
        op.create_table(
            "procurement_orders",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("order_key", sa.String(160), nullable=False, unique=True),
            sa.Column("supplier_key", sa.String(96), nullable=False),
            sa.Column("buyer_actor_key", sa.String(128), nullable=False),
            sa.Column("item_id", sa.Integer(), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("unit_cost_minor", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(24), nullable=False, server_default="open"),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("ledger_transaction_id", sa.Integer(), nullable=True),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        )
        op.create_index(
            "ix_procurement_orders_supplier_status",
            "procurement_orders",
            ["supplier_key", "status"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_table("procurement_orders"):
        op.drop_table("procurement_orders")
    if inspector.has_table("procurement_suppliers"):
        op.drop_table("procurement_suppliers")
