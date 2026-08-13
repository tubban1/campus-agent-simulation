"""Add open world geospatial fields and spatial import batches.

Revision ID: 20260809_0035
Revises: 20260731_0034
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260809_0035"
down_revision = "20260731_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    columns = [col["name"] for col in inspector.get_columns("spatial_nodes")]
    if "world_key" not in columns:
        op.add_column(
            "spatial_nodes",
            sa.Column(
                "world_key",
                sa.String(length=64),
                nullable=False,
                server_default="default",
            ),
        )
    if "longitude" not in columns:
        op.add_column("spatial_nodes", sa.Column("longitude", sa.Float(), nullable=True))
    if "latitude" not in columns:
        op.add_column("spatial_nodes", sa.Column("latitude", sa.Float(), nullable=True))
    if "elevation_m" not in columns:
        op.add_column(
            "spatial_nodes",
            sa.Column(
                "elevation_m",
                sa.Float(),
                nullable=False,
                server_default="0",
            ),
        )
    if "geometry_json" not in columns:
        op.add_column("spatial_nodes", sa.Column("geometry_json", sa.JSON(), nullable=True))
    if "source_element_id" not in columns:
        op.add_column(
            "spatial_nodes",
            sa.Column("source_element_id", sa.String(length=120), nullable=True),
        )

    indexes = [idx["name"] for idx in inspector.get_indexes("spatial_nodes")]
    if "ix_spatial_nodes_world_key" not in indexes:
        op.create_index("ix_spatial_nodes_world_key", "spatial_nodes", ["world_key"])

    if not inspector.has_table("spatial_import_batches"):
        op.create_table(
            "spatial_import_batches",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("batch_key", sa.String(length=80), nullable=False),
            sa.Column("world_key", sa.String(length=64), nullable=False),
            sa.Column("source", sa.String(length=255), nullable=False),
            sa.Column("license", sa.String(length=255), nullable=False),
            sa.Column("original_crs", sa.String(length=64), nullable=False),
            sa.Column("projection_meta", sa.JSON(), nullable=False),
            sa.Column("nodes_count", sa.Integer(), nullable=False),
            sa.Column("edges_count", sa.Integer(), nullable=False),
            sa.Column("features_count", sa.Integer(), nullable=False),
            sa.Column("quality_meta", sa.JSON(), nullable=False),
            sa.Column(
                "imported_at",
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
            sa.UniqueConstraint("batch_key", name="uq_spatial_import_batches_key"),
        )
        op.create_index("ix_spatial_import_batches_world", "spatial_import_batches", ["world_key"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table("spatial_import_batches"):
        op.drop_index("ix_spatial_import_batches_world", table_name="spatial_import_batches")
        op.drop_table("spatial_import_batches")

    indexes = [idx["name"] for idx in inspector.get_indexes("spatial_nodes")]
    if "ix_spatial_nodes_world_key" in indexes:
        op.drop_index("ix_spatial_nodes_world_key", table_name="spatial_nodes")

    columns = [col["name"] for col in inspector.get_columns("spatial_nodes")]
    for col in ("source_element_id", "geometry_json", "elevation_m", "latitude", "longitude", "world_key"):
        if col in columns:
            op.drop_column("spatial_nodes", col)
