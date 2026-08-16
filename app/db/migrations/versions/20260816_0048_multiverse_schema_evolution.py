"""Evolve campus schema into multiverse schemas with world_key isolation.

Revision ID: 20260816_0048
Revises: 20260813_0047
"""

from alembic import op
import sqlalchemy as sa


revision = "20260816_0048"
down_revision = "20260813_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Create world_environment_states table
    if not inspector.has_table("world_environment_states"):
        op.create_table(
            "world_environment_states",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("world_key", sa.String(96), nullable=False, server_default="default"),
            sa.Column("sector_id", sa.String(96), nullable=False, server_default="sector-01"),
            sa.Column("day", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("weather", sa.String(48), nullable=False, server_default="晴"),
            sa.Column("temperature", sa.Integer(), nullable=False, server_default="24"),
            sa.Column("humidity", sa.Float(), nullable=False, server_default="50.0"),
            sa.Column("rainfall", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("wind_speed", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("social_atmosphere", sa.String(96), nullable=False, server_default="平稳"),
            sa.Column("economic_pressure", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("activity_heat", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("traffic_status", sa.String(48), nullable=False, server_default="正常"),
            sa.Column("resource_pressure", sa.Integer(), nullable=False, server_default="45"),
            sa.Column("safety_level", sa.Integer(), nullable=False, server_default="90"),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_world_env_states_world_day",
            "world_environment_states",
            ["world_key", "day"],
        )

    # 2. Create world_organizations table
    if not inspector.has_table("world_organizations"):
        op.create_table(
            "world_organizations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("world_key", sa.String(96), nullable=False, server_default="default"),
            sa.Column("sector_id", sa.String(96), nullable=False, server_default="sector-01"),
            sa.Column("name", sa.String(160), nullable=False),
            sa.Column("organization_type", sa.String(48), nullable=False),
            sa.Column("goal", sa.Text(), nullable=False, server_default=""),
            sa.Column("budget", sa.Integer(), nullable=False, server_default="1000"),
            sa.Column("parent_org_id", sa.Integer(), nullable=True),
            sa.Column("resources", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("schedule", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(24), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_world_organizations_world_status",
            "world_organizations",
            ["world_key", "status"],
        )

    # 3. Create world_schedule_rules table
    if not inspector.has_table("world_schedule_rules"):
        op.create_table(
            "world_schedule_rules",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("world_key", sa.String(96), nullable=False, server_default="default"),
            sa.Column("role_group", sa.String(48), nullable=False),
            sa.Column("action_type", sa.String(48), nullable=False),
            sa.Column("start_hour", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("end_hour", sa.Integer(), nullable=False, server_default="24"),
            sa.Column("base_weight", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("sector_template", sa.String(96), nullable=False, server_default="default"),
            sa.Column("status", sa.String(24), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_world_schedule_rules_ctx",
            "world_schedule_rules",
            ["world_key", "role_group", "action_type", "start_hour", "end_hour"],
        )

    # Backfill default seed data from existing campus tables into new world_ tables
    try:
        if inspector.has_table("campus_organizations"):
            conn.execute(sa.text(
                "INSERT INTO world_organizations (world_key, sector_id, name, organization_type, goal, budget, resources, schedule, status) "
                "SELECT 'default', 'sector-01', name, organization_type, goal, budget, resources, schedule, status "
                "FROM campus_organizations "
                "ON CONFLICT DO NOTHING;"
            ))
    except Exception:
        pass

    try:
        if inspector.has_table("campus_schedule_rules"):
            conn.execute(sa.text(
                "INSERT INTO world_schedule_rules (world_key, role_group, action_type, start_hour, end_hour, base_weight) "
                "SELECT 'default', role_group, action_type, start_hour, end_hour, base_weight "
                "FROM campus_schedule_rules "
                "ON CONFLICT DO NOTHING;"
            ))
    except Exception:
        pass

    try:
        if inspector.has_table("campus_state"):
            conn.execute(sa.text(
                "INSERT INTO world_environment_states (world_key, sector_id, day, weather, temperature, humidity, rainfall, wind_speed, social_atmosphere, activity_heat, traffic_status, resource_pressure, safety_level) "
                "SELECT 'default', 'sector-01', day, weather, temperature, relative_humidity_2m, rainfall, wind_speed_10m, campus_mood, activity_heat, traffic_status, resource_pressure, safety_level "
                "FROM campus_state "
                "ON CONFLICT DO NOTHING;"
            ))
    except Exception:
        pass


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_table("world_schedule_rules"):
        op.drop_table("world_schedule_rules")
    if inspector.has_table("world_organizations"):
        op.drop_table("world_organizations")
    if inspector.has_table("world_environment_states"):
        op.drop_table("world_environment_states")
