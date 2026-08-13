"""Add lightweight daily nutrition, hydration and load signals.

Revision ID: 20260813_0040
Revises: 20260813_0039
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_0040"
down_revision = "20260813_0039"
branch_labels = None
depends_on = None


FIELDS = {
    "hydration": 25.0,      # 0 = hydrated, 100 = acute dehydration
    "nutrition": 78.0,     # 0 = poor nutritional reserve, 100 = adequate
    "activity_load": 18.0, # rolling physical load
    "illness_load": 0.0,   # acute illness burden; not a diagnosis
}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("agent_body_states"):
        return
    columns = {column["name"] for column in inspector.get_columns("agent_body_states")}
    for field, default in FIELDS.items():
        if field not in columns:
            op.add_column(
                "agent_body_states",
                sa.Column(field, sa.Float(), nullable=False, server_default=str(default)),
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("agent_body_states"):
        return
    columns = {column["name"] for column in inspector.get_columns("agent_body_states")}
    for field in FIELDS:
        if field in columns:
            op.drop_column("agent_body_states", field)
