"""Add spatial_affordances and atomic plan columns for Phase 3.6A.

Revision ID: 20260810_0036
Revises: 20260809_0035
Create Date: 2026-08-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260810_0036"
down_revision = "20260809_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Ensure spatial_nodes primary key constraint exists
    pk_constraint = inspector.get_pk_constraint("spatial_nodes")
    if not pk_constraint or not pk_constraint.get("constrained_columns"):
        try:
            op.create_primary_key("pk_spatial_nodes", "spatial_nodes", ["id"])
        except Exception:
            pass

    # 2. Create spatial_affordances table if not exists
    if not inspector.has_table("spatial_affordances"):
        op.create_table(
            "spatial_affordances",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("world_key", sa.String(length=64), nullable=False, server_default="default"),
            sa.Column(
                "node_id",
                sa.Integer(),
                sa.ForeignKey("spatial_nodes.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("affordance_key", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("requirements", sa.JSON(), nullable=False),
            sa.Column("effects", sa.JSON(), nullable=False),
            sa.Column("capacity", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("node_id", "affordance_key", name="uq_spatial_affordance_node_key"),
        )
        op.create_index("ix_spatial_affordances_world", "spatial_affordances", ["world_key"])

    # 3. Add atomic action plan fields to agent_action_plans table
    if not inspector.has_table("agent_action_plans"):
        op.create_table(
            "agent_action_plans",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "resident_id",
                sa.Integer(),
                sa.ForeignKey("residents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("goal_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="planning"),
            sa.Column("target_affordance_key", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("target_node_id", sa.Integer(), nullable=True),
            sa.Column("current_step_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("steps_json", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_agent_action_plans_resident", "agent_action_plans", ["resident_id"])
    else:
        existing_cols = [c["name"] for c in inspector.get_columns("agent_action_plans")]
        if "goal_id" not in existing_cols:
            op.add_column("agent_action_plans", sa.Column("goal_id", sa.Integer(), nullable=True))
        if "target_affordance_key" not in existing_cols:
            op.add_column("agent_action_plans", sa.Column("target_affordance_key", sa.String(length=64), nullable=False, server_default=""))
        if "target_node_id" not in existing_cols:
            op.add_column("agent_action_plans", sa.Column("target_node_id", sa.Integer(), nullable=True))
        if "current_step_index" not in existing_cols:
            op.add_column("agent_action_plans", sa.Column("current_step_index", sa.Integer(), nullable=False, server_default="0"))
        if "steps_json" not in existing_cols:
            op.add_column("agent_action_plans", sa.Column("steps_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table("agent_action_plans"):
        existing_cols = [c["name"] for c in inspector.get_columns("agent_action_plans")]
        for col in ("steps_json", "current_step_index", "target_node_id", "target_affordance_key", "goal_id"):
            if col in existing_cols:
                op.drop_column("agent_action_plans", col)

    if inspector.has_table("spatial_affordances"):
        op.drop_index("ix_spatial_affordances_world", table_name="spatial_affordances")
        op.drop_table("spatial_affordances")
