"""Persist wind and humidity alongside the campus weather snapshot.

Revision ID: 20260812_0038
Revises: 20260812_0037
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260812_0038"
down_revision = "20260812_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("campus_state"):
        return
    columns = {column["name"] for column in inspector.get_columns("campus_state")}
    if "wind_speed_10m" not in columns:
        op.add_column(
            "campus_state",
            sa.Column("wind_speed_10m", sa.Float(), nullable=False, server_default="0"),
        )
    if "relative_humidity_2m" not in columns:
        op.add_column(
            "campus_state",
            sa.Column(
                "relative_humidity_2m", sa.Float(), nullable=False, server_default="50"
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("campus_state"):
        return
    columns = {column["name"] for column in inspector.get_columns("campus_state")}
    if "relative_humidity_2m" in columns:
        op.drop_column("campus_state", "relative_humidity_2m")
    if "wind_speed_10m" in columns:
        op.drop_column("campus_state", "wind_speed_10m")
