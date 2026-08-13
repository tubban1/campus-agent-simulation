"""World snapshot persistence operations.

Dependencies are configured by the application composition root.
"""

from app.world_state.snapshot_catalog import *

_MODULE_NAME = __name__

def configure(**bindings):
    module_globals = globals()
    for name, value in bindings.items():
        if name.startswith("__"):
            continue
        current = module_globals.get(name)
        if callable(current) and getattr(current, "__module__", None) == _MODULE_NAME:
            continue
        module_globals[name] = value
    module_globals["__name__"] = _MODULE_NAME


def snapshot_table_exists(conn, table_name):
    return bool(conn.execute(f"PRAGMA table_info({table_name})").fetchall())


def snapshot_state_tables(conn):
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
            **RESILIENCE_SNAPSHOT_STATE_TABLES,
            **POPULATION_SNAPSHOT_STATE_TABLES,
            **EXTERNAL_WORLD_SNAPSHOT_STATE_TABLES,
            **LONGITUDINAL_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
            **RESILIENCE_SNAPSHOT_STATE_TABLES,
            **POPULATION_SNAPSHOT_STATE_TABLES,
            **EXTERNAL_WORLD_SNAPSHOT_STATE_TABLES,
            **LONGITUDINAL_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in SPATIAL_SNAPSHOT_STATE_TABLES
    ):
        return {**SNAPSHOT_STATE_TABLES, **SPATIAL_SNAPSHOT_STATE_TABLES}
    return SNAPSHOT_STATE_TABLES


def capture_objective_world_state(conn, ensure_schema=True, state_tables=None):
    if ensure_schema:
        ensure_campus_state_table(conn)
        ensure_space_system(conn)
        ensure_agent_news_system(conn)
        ensure_external_information_system(conn)
    state = {}
    for table_name, order_by in (
        state_tables or snapshot_state_tables(conn)
    ).items():
        where_clause = " WHERE status = 'pending'" if table_name == "world_delayed_effects" else ""
        rows = conn.execute(
            f"SELECT * FROM {table_name}{where_clause} ORDER BY {order_by}"
        ).fetchall()
        state[table_name] = [dict(row) for row in rows]
    return state


def decode_world_snapshot(row, include_state=False):
    item = dict(row)
    item["metadata"] = load_json_text(item.pop("metadata_json", "{}"), {})
    if include_state:
        item["state"] = load_json_text(item.pop("state_json", "{}"), {})
    else:
        item.pop("state_json", None)
    return item


def create_world_snapshot_record(
    conn,
    reason="manual checkpoint",
    snapshot_type="manual_checkpoint",
    run_id="",
    branch_key="main",
    parent_snapshot_id=None,
    external_data_version="",
    metadata=None,
):
    ensure_world_runtime_tables(conn)
    if parent_snapshot_id:
        parent = conn.execute(
            "SELECT id FROM world_snapshots WHERE id = ?",
            (parent_snapshot_id,),
        ).fetchone()
        if not parent:
            raise ValueError("父快照不存在")
    runtime = dict(
        conn.execute("SELECT * FROM world_runtime WHERE id = ?", (WORLD_RUNTIME_ID,)).fetchone()
    )
    active_config = get_active_environment_config(conn)
    event_row = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS value FROM world_event_stream"
    ).fetchone()
    tick_row = conn.execute("SELECT id FROM world_ticks ORDER BY id DESC LIMIT 1").fetchone()
    state = capture_objective_world_state(conn)
    schema_version = (
        "world-snapshot-v16-household-credit"
        if all(table in state for table in CREDIT_SNAPSHOT_STATE_TABLES)
        else (
            "world-snapshot-v15-market-pricing"
            if all(table in state for table in MARKET_SNAPSHOT_STATE_TABLES)
            else (
                "world-snapshot-v14-budget-choice"
                if all(table in state for table in BUDGET_SNAPSHOT_STATE_TABLES)
                else (
                    "world-snapshot-v13-labor-runtime"
                    if all(table in state for table in LABOR_SNAPSHOT_STATE_TABLES)
                    else (
                        "world-snapshot-v12-supply-runtime"
                        if all(table in state for table in SUPPLY_SNAPSHOT_STATE_TABLES)
                        else (
                            "world-snapshot-v11-organization-runtime"
                            if all(table in state for table in ORGANIZATION_SNAPSHOT_STATE_TABLES)
                            else (
                                "world-snapshot-v10-ledger-controls"
                                if all(table in state for table in ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES)
                                else (
                                    "world-snapshot-v9-economy"
                                    if all(table in state for table in ECONOMY_SNAPSHOT_STATE_TABLES)
                                    else (
                                        "world-snapshot-v8-capability"
                                        if all(table in state for table in CAPABILITY_SNAPSHOT_STATE_TABLES)
                                        else (
                                            "world-snapshot-v7-perception"
                                            if all(table in state for table in PERCEPTION_SNAPSHOT_STATE_TABLES)
                                            else (
                                                "world-snapshot-v6-body"
                                                if all(table in state for table in BODY_SNAPSHOT_STATE_TABLES)
                                                else (
                                                    "world-snapshot-v5-admission"
                                                    if all(
                                                        table in state
                                                        for table in SPATIAL_SNAPSHOT_STATE_TABLES
                                                    )
                                                    else (
                                                        "world-snapshot-v4-spatial"
                                                        if all(
                                                            table in state
                                                            for table in SPATIAL_FOUNDATION_SNAPSHOT_TABLES
                                                        )
                                                        else "world-snapshot-v3"
                                                    )
                                                )
                                            )
                                        )
                                    )
                                )
                            )
                        )
                    )
                )
            )
        )
    )
    if all(table in state for table in PUBLIC_POLICY_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v17-public-policy"
    if all(table in state for table in SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v18-social-institutions"
    if all(table in state for table in MACRO_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v19-macro-reconciliation"
    if all(table in state for table in ADAPTATION_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v23-institution-evolution"
    if all(table in state for table in RESILIENCE_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v24-shock-recovery"
    if all(table in state for table in POPULATION_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v25-population-mobility"
    if all(table in state for table in EXTERNAL_WORLD_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v30-external-governance"
    if all(table in state for table in LONGITUDINAL_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v31-longitudinal-paths"
    state_json = canonical_json(state)
    event_cursor = int(event_row["value"] or 0)
    effective_branch_key = branch_key or runtime.get("active_branch_key") or "main"
    snapshot_metadata = {
        "table_counts": {name: len(rows) for name, rows in state.items()},
        "environment_checksum": active_config["checksum"] if active_config else "",
        "state_table_count": len(state),
        "restorable": True,
        **(metadata or {}),
    }
    cursor = conn.execute(
        """
        INSERT INTO world_snapshots
        (run_id, snapshot_type, world_time, day, tick_id, reason, state_json,
         schema_version, environment_config_id, environment_version, random_seed,
         external_data_version, event_cursor, parent_snapshot_id, branch_key,
         checksum, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id or runtime.get("active_run_id") or "",
            snapshot_type,
            runtime.get("world_time") or get_world_now().isoformat(),
            get_current_day(conn),
            tick_row["id"] if tick_row else None,
            reason,
            state_json,
            schema_version,
            runtime.get("environment_config_id"),
            runtime.get("environment_version") or "",
            runtime.get("random_seed") or "",
            external_data_version,
            event_cursor,
            parent_snapshot_id,
            effective_branch_key,
            content_checksum(state_json),
            canonical_json(snapshot_metadata),
        ),
    )
    snapshot_id = cursor.lastrowid
    conn.execute(
        """
        UPDATE world_branches
        SET head_snapshot_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE branch_key = ?
        """,
        (snapshot_id, effective_branch_key),
    )
    return decode_world_snapshot(
        conn.execute("SELECT * FROM world_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    )


SNAPSHOT_UPSERT_KEYS = {
    "residents": ("id",),
    "world_runtime": ("id",),
    "world_update_schedules": ("id",),
    "spatial_nodes": ("id",),
    "spatial_edges": ("id",),
    "spatial_resources": ("id",),
    "agent_spatial_capabilities": ("resident_id",),
    "agent_spatial_states": ("resident_id",),
    "agent_body_states": ("resident_id",),
    "agent_observations": ("id",),
    "agent_belief_states": ("id",),
    "agent_spatial_memories": ("id",),
    "agent_capability_profiles": ("resident_id",),
    "agent_opportunity_access": ("id",),
}


def snapshot_row_or_error(conn, snapshot_id):
    row = conn.execute(
        "SELECT * FROM world_snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    if not row:
        raise ValueError("世界快照不存在")
    if row["checksum"] != content_checksum(row["state_json"]):
        raise ValueError("世界快照 checksum 校验失败")
    state = load_json_text(row["state_json"], {})
    if not isinstance(state, dict):
        raise ValueError("世界快照状态格式无效")
    if row["schema_version"] == "world-snapshot-v31-longitudinal-paths":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
            **RESILIENCE_SNAPSHOT_STATE_TABLES,
            **POPULATION_SNAPSHOT_STATE_TABLES,
            **EXTERNAL_WORLD_SNAPSHOT_STATE_TABLES,
            **LONGITUDINAL_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v30-external-governance":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
            **RESILIENCE_SNAPSHOT_STATE_TABLES,
            **POPULATION_SNAPSHOT_STATE_TABLES,
            **EXTERNAL_WORLD_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v25-population-mobility":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
            **RESILIENCE_SNAPSHOT_STATE_TABLES,
            **POPULATION_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v24-shock-recovery":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
            **RESILIENCE_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v23-institution-evolution":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v19-macro-reconciliation":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v18-social-institutions":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v17-public-policy":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v16-household-credit":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v15-market-pricing":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v14-budget-choice":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v13-labor-runtime":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v12-supply-runtime":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v11-organization-runtime":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v10-ledger-controls":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v9-economy":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v8-capability":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v7-perception":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v6-body":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v5-admission":
        expected_tables = {**SNAPSHOT_STATE_TABLES, **SPATIAL_SNAPSHOT_STATE_TABLES}
    elif row["schema_version"] == "world-snapshot-v4-spatial":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_FOUNDATION_SNAPSHOT_TABLES,
        }
    else:
        expected_tables = SNAPSHOT_STATE_TABLES
    missing = [table for table in expected_tables if table not in state]
    if missing:
        raise ValueError(f"快照版本不支持完整恢复，缺少状态表：{', '.join(missing[:6])}")
    return row, state, expected_tables


def insert_snapshot_rows(conn, table_name, rows):
    if not rows:
        return
    table_columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for row in rows:
        columns = [column for column in row if column in table_columns]
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(row[column] for column in columns),
        )


def upsert_snapshot_rows(conn, table_name, rows, key_columns):
    table_columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for row in rows:
        columns = [column for column in row if column in table_columns]
        mutable_columns = [column for column in columns if column not in key_columns]
        where_clause = " AND ".join(f"{column} = ?" for column in key_columns)
        values = [row[column] for column in mutable_columns]
        values.extend(row[column] for column in key_columns)
        cursor = conn.execute(
            f"UPDATE {table_name} SET "
            + ", ".join(f"{column} = ?" for column in mutable_columns)
            + f" WHERE {where_clause}",
            tuple(values),
        )
        if not cursor.rowcount:
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(row[column] for column in columns),
            )


def restore_world_snapshot_state(conn, snapshot_id, active_branch_key=None, active_run_id=""):
    ensure_world_runtime_tables(conn)
    snapshot_row, state, state_tables = snapshot_row_or_error(conn, snapshot_id)
    current_resident_ids = {
        int(row["id"]) for row in conn.execute("SELECT id FROM residents").fetchall()
    }
    snapshot_resident_ids = {int(row["id"]) for row in state["residents"]}
    if current_resident_ids != snapshot_resident_ids:
        raise ValueError("当前版本仅支持居民拓扑一致的快照恢复")

    conn.execute("SAVEPOINT world_snapshot_restore")
    try:
        replace_tables = [
            table
            for table in state_tables
            if table not in SNAPSHOT_UPSERT_KEYS
        ]
        for table_name in reversed(replace_tables):
            conn.execute(f"DELETE FROM {table_name}")
        for table_name in state_tables:
            rows = state[table_name]
            key_columns = SNAPSHOT_UPSERT_KEYS.get(table_name)
            if key_columns:
                upsert_snapshot_rows(conn, table_name, rows, key_columns)
            else:
                insert_snapshot_rows(conn, table_name, rows)

        effective_branch = (
            active_branch_key
            or snapshot_row["branch_key"]
            or "main"
        )
        conn.execute(
            """
            UPDATE world_runtime
            SET status = 'paused', active_branch_key = ?, active_run_id = ?,
                world_time = ?, last_tick_started_at = '',
                last_tick_completed_at = '', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                effective_branch,
                active_run_id,
                snapshot_row["world_time"],
                WORLD_RUNTIME_ID,
            ),
        )
        restored_state = capture_objective_world_state(
            conn,
            ensure_schema=False,
            state_tables=state_tables,
        )
        restored_checksum = content_checksum(canonical_json(restored_state))
        expected_checksum = content_checksum(snapshot_row["state_json"])
        if restored_checksum != expected_checksum:
            runtime_rows = restored_state.get("world_runtime", [])
            snapshot_runtime_rows = state.get("world_runtime", [])
            for rows in (runtime_rows, snapshot_runtime_rows):
                if rows:
                    rows[0]["status"] = "paused"
                    rows[0]["active_branch_key"] = effective_branch
                    rows[0]["active_run_id"] = active_run_id
                    rows[0]["last_tick_started_at"] = ""
                    rows[0]["last_tick_completed_at"] = ""
                    rows[0].pop("updated_at", None)
                    rows[0]["world_time"] = snapshot_row["world_time"]
            restored_checksum = content_checksum(canonical_json(restored_state))
            expected_checksum = content_checksum(canonical_json(state))
            if restored_checksum != expected_checksum:
                raise ValueError("快照恢复后的状态校验失败")
        conn.execute("RELEASE SAVEPOINT world_snapshot_restore")
        return {
            "snapshot_id": snapshot_id,
            "branch_key": effective_branch,
            "schema_version": snapshot_row["schema_version"],
            "table_counts": {
                table: len(rows) for table, rows in state.items()
            },
        }
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT world_snapshot_restore")
        conn.execute("RELEASE SAVEPOINT world_snapshot_restore")
        raise


def decode_world_branch(row):
    item = dict(row)
    item["metadata"] = load_json_text(item.pop("metadata_json", "{}"), {})
    return item


def create_world_branch_record(conn, branch_key, name, source_snapshot_id, metadata=None):
    source_row, _, _ = snapshot_row_or_error(conn, source_snapshot_id)
    existing = conn.execute(
        "SELECT id FROM world_branches WHERE branch_key = ?",
        (branch_key,),
    ).fetchone()
    if existing:
        raise ValueError("世界分支标识已存在")
    runtime = conn.execute(
        "SELECT * FROM world_runtime WHERE id = ?",
        (WORLD_RUNTIME_ID,),
    ).fetchone()
    parent_branch_key = runtime["active_branch_key"] or "main"
    run_id = f"branch-{uuid4()}"
    clone_metadata = load_json_text(source_row["metadata_json"], {})
    clone_metadata.update(
        {
            "forked_from_snapshot_id": source_snapshot_id,
            "isolated_branch": True,
            **(metadata or {}),
        }
    )
    clone_cursor = conn.execute(
        """
        INSERT INTO world_snapshots
        (run_id, snapshot_type, world_time, day, tick_id, reason, state_json,
         schema_version, environment_config_id, environment_version, random_seed,
         external_data_version, event_cursor, parent_snapshot_id, branch_key,
         checksum, metadata_json)
        VALUES (?, 'branch_seed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            source_row["world_time"],
            source_row["day"],
            source_row["tick_id"],
            f"从快照 #{source_snapshot_id} 创建隔离分支",
            source_row["state_json"],
            source_row["schema_version"],
            source_row["environment_config_id"],
            source_row["environment_version"],
            source_row["random_seed"],
            source_row["external_data_version"],
            source_row["event_cursor"],
            source_snapshot_id,
            branch_key,
            source_row["checksum"],
            canonical_json(clone_metadata),
        ),
    )
    head_snapshot_id = clone_cursor.lastrowid
    branch_cursor = conn.execute(
        """
        INSERT INTO world_branches
        (branch_key, name, parent_branch_key, base_snapshot_id, head_snapshot_id,
         run_id, status, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, 'ready', ?)
        """,
        (
            branch_key,
            name or branch_key,
            parent_branch_key,
            source_snapshot_id,
            head_snapshot_id,
            run_id,
            canonical_json(metadata or {}),
        ),
    )
    conn.execute(
        """
        INSERT INTO experiment_runs
        (run_id, experiment_name, control_or_treatment, random_seed,
         environment_version, world_rules_version, environment_config_id,
         source_snapshot_id, parent_run_id, branch_key, event_cursor_start,
         status, metadata_json)
        VALUES (?, ?, 'branch', ?, ?, 'world-runtime-v1', ?, ?, ?, ?, ?, 'paused', ?)
        """,
        (
            run_id,
            name or branch_key,
            source_row["random_seed"],
            source_row["environment_version"],
            source_row["environment_config_id"],
            source_snapshot_id,
            runtime["active_run_id"] or "",
            branch_key,
            source_row["event_cursor"],
            canonical_json(metadata or {}),
        ),
    )
    return decode_world_branch(
        conn.execute(
            "SELECT * FROM world_branches WHERE id = ?",
            (branch_cursor.lastrowid,),
        ).fetchone()
    )
