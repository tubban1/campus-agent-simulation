from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    func,
)

from app.db.metadata import metadata


spatial_nodes = Table(
    "spatial_nodes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("code", String(64), nullable=False, unique=True),
    Column("name", String(120), nullable=False),
    Column("node_type", String(32), nullable=False),
    Column("parent_id", ForeignKey("spatial_nodes.id", ondelete="SET NULL")),
    Column("world_key", String(64), nullable=False, default="default", index=True),
    Column("x", Float, nullable=False),
    Column("y", Float, nullable=False, default=0),
    Column("z", Float, nullable=False),
    Column("longitude", Float, nullable=True),
    Column("latitude", Float, nullable=True),
    Column("elevation_m", Float, nullable=False, default=0.0),
    Column("geometry_json", JSON, nullable=True),
    Column("source_element_id", String(120), nullable=True),
    Column("radius", Float, nullable=False),
    Column("capacity", Integer, nullable=False, default=0),
    Column("status", String(32), nullable=False, default="open"),
    Column("properties", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint("capacity >= 0", name="spatial_nodes_capacity_nonnegative"),
    CheckConstraint("radius > 0", name="spatial_nodes_radius_positive"),
    CheckConstraint("parent_id IS NULL OR parent_id <> id", name="spatial_nodes_parent_not_self"),
)

spatial_import_batches = Table(
    "spatial_import_batches",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_key", String(80), nullable=False, unique=True),
    Column("world_key", String(64), nullable=False, index=True),
    Column("source", String(120), nullable=False),
    Column("license", String(120), nullable=False),
    Column("original_crs", String(32), nullable=False, default="EPSG:4326"),
    Column("projection_meta", JSON, nullable=False),
    Column("nodes_count", Integer, nullable=False, default=0),
    Column("edges_count", Integer, nullable=False, default=0),
    Column("features_count", Integer, nullable=False, default=0),
    Column("quality_meta", JSON, nullable=False),
    Column("imported_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

spatial_edges = Table(
    "spatial_edges",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "from_node_id",
        ForeignKey("spatial_nodes.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "to_node_id",
        ForeignKey("spatial_nodes.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("distance_meters", Float, nullable=False),
    Column("base_minutes", Float, nullable=False),
    Column("bidirectional", Boolean, nullable=False, default=True),
    Column("status", String(32), nullable=False, default="open"),
    Column("congestion_factor", Float, nullable=False, default=1.0),
    Column("weather_factor", Float, nullable=False, default=1.0),
    Column("properties", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    UniqueConstraint("from_node_id", "to_node_id", name="uq_spatial_edges_from_to"),
    CheckConstraint("from_node_id <> to_node_id", name="spatial_edges_distinct_nodes"),
    CheckConstraint("distance_meters > 0", name="spatial_edges_distance_positive"),
    CheckConstraint("base_minutes > 0", name="spatial_edges_minutes_positive"),
    CheckConstraint("congestion_factor > 0", name="spatial_edges_congestion_positive"),
    CheckConstraint("weather_factor > 0", name="spatial_edges_weather_positive"),
)

spatial_physical_states = Table(
    "spatial_physical_states",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("world_key", String(64), nullable=False, index=True),
    Column("node_id", ForeignKey("spatial_nodes.id", ondelete="CASCADE"), nullable=False),
    Column("temperature_c", Float, nullable=True),
    Column("precipitation", Float, nullable=False, default=0.0),
    Column("illumination", Float, nullable=False, default=1.0),
    Column("noise_db", Float, nullable=False, default=30.0),
    Column("crowd_density", Float, nullable=False, default=0.0),
    Column("air_quality", Float, nullable=False, default=100.0),
    Column("access_status", String(32), nullable=False, default="open"),
    Column("source", String(64), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("version", Integer, nullable=False, default=1),
    UniqueConstraint("world_key", "node_id", name="uq_spatial_physical_state_world_node"),
)

spatial_edge_physical_states = Table(
    "spatial_edge_physical_states",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("world_key", String(64), nullable=False, index=True),
    Column("edge_id", ForeignKey("spatial_edges.id", ondelete="CASCADE"), nullable=False),
    Column("access_status", String(32), nullable=False, default="open"),
    Column("travel_factor", Float, nullable=False, default=1.0),
    Column("source", String(64), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("version", Integer, nullable=False, default=1),
    UniqueConstraint("world_key", "edge_id", name="uq_spatial_edge_physical_state_world_edge"),
)

agent_spatial_capabilities = Table(
    "agent_spatial_capabilities",
    metadata,
    Column(
        "resident_id",
        ForeignKey("residents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("base_speed_m_per_min", Float, nullable=False, default=78.0),
    Column("mobility_class", String(32), nullable=False, default="standard"),
    Column("accessibility_needs", JSON, nullable=False),
    Column("perception_radius_m", Float, nullable=False, default=35.0),
    Column("hearing_radius_m", Float, nullable=False, default=20.0),
    Column("source", String(64), nullable=False, default="seeded"),
    Column("version", Integer, nullable=False, default=1),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint(
        "base_speed_m_per_min > 0", name="agent_spatial_capabilities_speed_positive"
    ),
    CheckConstraint(
        "perception_radius_m > 0",
        name="agent_spatial_capabilities_perception_positive",
    ),
    CheckConstraint(
        "hearing_radius_m > 0",
        name="agent_spatial_capabilities_hearing_positive",
    ),
    CheckConstraint("version > 0", name="agent_spatial_capabilities_version_positive"),
)

agent_spatial_states = Table(
    "agent_spatial_states",
    metadata,
    Column(
        "resident_id",
        ForeignKey("residents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "current_node_id",
        ForeignKey("spatial_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("origin_node_id", ForeignKey("spatial_nodes.id", ondelete="SET NULL")),
    Column("target_node_id", ForeignKey("spatial_nodes.id", ondelete="SET NULL")),
    Column("x", Float, nullable=False),
    Column("y", Float, nullable=False, default=0),
    Column("z", Float, nullable=False),
    Column("facing_x", Float, nullable=False, default=0),
    Column("facing_z", Float, nullable=False, default=1),
    Column("movement_status", String(32), nullable=False, default="idle"),
    Column("path", JSON, nullable=False),
    Column("path_index", Integer, nullable=False, default=0),
    Column("progress", Float, nullable=False, default=0),
    Column("route_distance_meters", Float, nullable=False, default=0),
    Column("remaining_distance_meters", Float, nullable=False, default=0),
    Column("updated_tick", Integer, nullable=False, default=0),
    Column("version", Integer, nullable=False, default=1),
    Column("branch_key", String(80), nullable=False, default="main"),
    Column("planned_at", DateTime(timezone=True)),
    Column("started_at", DateTime(timezone=True)),
    Column("last_progress_at", DateTime(timezone=True)),
    Column("estimated_arrival_at", DateTime(timezone=True)),
    Column("replan_count", Integer, nullable=False, default=0),
    Column("last_replan_reason", String(240), nullable=False, default=""),
    Column("interrupted_reason", String(240), nullable=False, default=""),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint("path_index >= 0", name="agent_spatial_states_path_index_nonnegative"),
    CheckConstraint(
        "progress >= 0 AND progress <= 1", name="agent_spatial_states_progress_range"
    ),
    CheckConstraint(
        "route_distance_meters >= 0",
        name="agent_spatial_states_route_distance_nonnegative",
    ),
    CheckConstraint(
        "remaining_distance_meters >= 0",
        name="agent_spatial_states_remaining_distance_nonnegative",
    ),
    CheckConstraint("updated_tick >= 0", name="agent_spatial_states_tick_nonnegative"),
    CheckConstraint("version > 0", name="agent_spatial_states_version_positive"),
    CheckConstraint(
        "replan_count >= 0", name="agent_spatial_states_replan_count_nonnegative"
    ),
)

agent_trajectories = Table(
    "agent_trajectories",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "experiment_run_id",
        ForeignKey("experiment_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("branch_key", String(80), nullable=False, default="main"),
    Column("tick_number", Integer, nullable=False),
    Column(
        "resident_id",
        ForeignKey("residents.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("node_id", ForeignKey("spatial_nodes.id", ondelete="SET NULL")),
    Column("x", Float, nullable=False),
    Column("y", Float, nullable=False),
    Column("z", Float, nullable=False),
    Column("movement_status", String(32), nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "experiment_run_id",
        "branch_key",
        "tick_number",
        "resident_id",
        name="uq_agent_trajectories_run_branch_tick_resident",
    ),
    CheckConstraint("tick_number >= 0", name="agent_trajectories_tick_nonnegative"),
)

spatial_resources = Table(
    "spatial_resources",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "node_id",
        ForeignKey("spatial_nodes.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("resource_key", String(80), nullable=False),
    Column("name", String(120), nullable=False),
    Column("capacity", Integer, nullable=False),
    Column("available_units", Integer, nullable=False),
    Column("service_rate_per_hour", Float, nullable=False),
    Column("status", String(32), nullable=False, default="available"),
    Column("properties", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("node_id", "resource_key", name="uq_spatial_resources_node_key"),
    CheckConstraint("capacity >= 0", name="spatial_resources_capacity_nonnegative"),
    CheckConstraint(
        "available_units >= 0 AND available_units <= capacity",
        name="spatial_resources_available_range",
    ),
    CheckConstraint(
        "service_rate_per_hour > 0",
        name="spatial_resources_service_rate_positive",
    ),
)

spatial_facility_states = Table(
    "spatial_facility_states",
    metadata,
    Column(
        "resource_id",
        ForeignKey("spatial_resources.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("open_hour", Integer, nullable=False, default=0),
    Column("close_hour", Integer, nullable=False, default=24),
    Column("condition", Float, nullable=False, default=100.0),
    Column("maintenance_status", String(32), nullable=False, default="operational"),
    Column("inventory_units", Integer, nullable=False, default=0),
    Column("inventory_capacity", Integer, nullable=False, default=0),
    Column("last_replenished_day", Integer, nullable=False, default=0),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("open_hour >= 0 AND open_hour <= 23", name="spatial_facility_open_hour_range"),
    CheckConstraint("close_hour >= 1 AND close_hour <= 24", name="spatial_facility_close_hour_range"),
    CheckConstraint("condition >= 0 AND condition <= 100", name="spatial_facility_condition_range"),
    CheckConstraint("inventory_units >= 0", name="spatial_facility_inventory_nonnegative"),
    CheckConstraint("inventory_capacity >= 0", name="spatial_facility_inventory_capacity_nonnegative"),
    CheckConstraint("inventory_units <= inventory_capacity", name="spatial_facility_inventory_range"),
)

spatial_admission_queue = Table(
    "spatial_admission_queue",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "resident_id",
        ForeignKey("residents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    Column(
        "node_id",
        ForeignKey("spatial_nodes.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "resource_id",
        ForeignKey("spatial_resources.id", ondelete="SET NULL"),
    ),
    Column("requested_at", DateTime(timezone=True), nullable=False),
    Column("queue_position", Integer, nullable=False),
    Column("patience_minutes", Float, nullable=False),
    Column("estimated_wait_minutes", Float, nullable=False),
    Column("reason_code", String(64), nullable=False),
    Column("branch_key", String(80), nullable=False, default="main"),
    Column("requested_tick", Integer, nullable=False, default=0),
    Column("status", String(32), nullable=False, default="waiting"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "queue_position > 0", name="spatial_admission_queue_position_positive"
    ),
    CheckConstraint(
        "patience_minutes > 0", name="spatial_admission_queue_patience_positive"
    ),
    CheckConstraint(
        "estimated_wait_minutes >= 0",
        name="spatial_admission_queue_wait_nonnegative",
    ),
    CheckConstraint(
        "requested_tick >= 0", name="spatial_admission_queue_tick_nonnegative"
    ),
)

agent_body_states = Table(
    "agent_body_states",
    metadata,
    Column(
        "resident_id",
        ForeignKey("residents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("hunger", Float, nullable=False),
    Column("fatigue", Float, nullable=False),
    Column("sleep_debt", Float, nullable=False),
    Column("stress", Float, nullable=False),
    Column("attention", Float, nullable=False),
    Column("social_energy", Float, nullable=False),
    Column("health", Float, nullable=False),
    Column("weather_exposure", Float, nullable=False),
    Column("hydration", Float, nullable=False, default=25.0),
    Column("nutrition", Float, nullable=False, default=78.0),
    Column("activity_load", Float, nullable=False, default=18.0),
    Column("illness_load", Float, nullable=False, default=0.0),
    Column("last_updated_at", DateTime(timezone=True)),
    Column("last_updated_tick", Integer, nullable=False, default=0),
    Column("source", String(64), nullable=False, default="seeded"),
    Column("version", Integer, nullable=False, default=1),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "hunger >= 0 AND hunger <= 100", name="agent_body_states_hunger_range"
    ),
    CheckConstraint(
        "fatigue >= 0 AND fatigue <= 100", name="agent_body_states_fatigue_range"
    ),
    CheckConstraint(
        "sleep_debt >= 0 AND sleep_debt <= 100",
        name="agent_body_states_sleep_debt_range",
    ),
    CheckConstraint(
        "stress >= 0 AND stress <= 100", name="agent_body_states_stress_range"
    ),
    CheckConstraint(
        "attention >= 0 AND attention <= 100",
        name="agent_body_states_attention_range",
    ),
    CheckConstraint(
        "social_energy >= 0 AND social_energy <= 100",
        name="agent_body_states_social_energy_range",
    ),
    CheckConstraint(
        "health >= 0 AND health <= 100", name="agent_body_states_health_range"
    ),
    CheckConstraint(
        "weather_exposure >= 0 AND weather_exposure <= 100",
        name="agent_body_states_weather_exposure_range",
    ),
    CheckConstraint(
        "last_updated_tick >= 0", name="agent_body_states_tick_nonnegative"
    ),
    CheckConstraint("version > 0", name="agent_body_states_version_positive"),
)


spatial_affordances = Table(
    "spatial_affordances",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("world_key", String(64), nullable=False, default="default", index=True),
    Column(
        "node_id",
        ForeignKey("spatial_nodes.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("affordance_key", String(64), nullable=False),
    Column("name", String(120), nullable=False),
    Column("requirements", JSON, nullable=False),
    Column("effects", JSON, nullable=False),
    Column("capacity", Integer, nullable=False, default=0),
    Column("status", String(32), nullable=False, default="open"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    UniqueConstraint("node_id", "affordance_key", name="uq_spatial_affordance_node_key"),
    CheckConstraint("capacity >= 0", name="spatial_affordances_capacity_nonnegative"),
)

agent_action_plans = Table(
    "agent_action_plans",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "resident_id",
        ForeignKey("residents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("goal_id", Integer, nullable=True),
    Column("status", String(32), nullable=False, default="planning"),
    Column("target_affordance_key", String(64), nullable=False, default=""),
    Column("target_node_id", Integer, nullable=True),
    Column("current_step_index", Integer, nullable=False, default=0),
    Column("steps_json", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint("current_step_index >= 0", name="agent_action_plans_step_index_nonnegative"),
)
