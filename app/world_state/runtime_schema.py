from datetime import datetime
from dataclasses import dataclass
from typing import Any, Mapping


_MODULE_NAME = __name__


@dataclass(frozen=True)
class RuntimeSchemaDependencies:
    """Explicit composition-root bindings for runtime schema operations."""

    values: Mapping[str, Any]

    def apply(self):
        configure(**dict(self.values))


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


def ensure_world_runtime_tables(conn, *, allow_ddl=False):
    global WORLD_SCHEMA_READY
    if WORLD_SCHEMA_READY:
        return
    with WORLD_SCHEMA_LOCK:
        if WORLD_SCHEMA_READY:
            return
        if using_postgres() and hasattr(conn, "_connection") and not allow_ddl:
            conn.execute(
                "SELECT 1 FROM world_runtime WHERE id = ?",
                (WORLD_RUNTIME_ID,),
            ).fetchone()
            conn.execute("SELECT 1 FROM world_event_stream LIMIT 1").fetchone()
            WORLD_SCHEMA_READY = True
            return
        ensure_social_system_tables(conn, allow_ddl=allow_ddl)
        if allow_ddl:
            conn.executescript(WORLD_RUNTIME_SQL)
            conn.executescript(RESEARCH_SYSTEM_SQL)
        ensure_table_columns(
            conn, "world_runtime", WORLD_RUNTIME_COLUMNS, allow_ddl=allow_ddl
        )
        ensure_table_columns(
            conn,
            "world_event_stream",
            WORLD_EVENT_STREAM_COLUMNS,
            allow_ddl=allow_ddl,
        )
        ensure_table_columns(
            conn, "world_snapshots", WORLD_SNAPSHOT_COLUMNS, allow_ddl=allow_ddl
        )
        ensure_table_columns(
            conn, "experiment_runs", EXPERIMENT_RUN_COLUMNS, allow_ddl=allow_ddl
        )
        conn.execute(
            """
            UPDATE world_event_stream
            SET root_event_id = id,
                occurred_at = CASE WHEN occurred_at = '' THEN created_at ELSE occurred_at END
            WHERE root_event_id IS NULL
            """
        )
        if allow_ddl:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_world_event_stream_parent ON world_event_stream(parent_event_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_world_event_stream_root ON world_event_stream(root_event_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_world_event_stream_source ON world_event_stream(source_type, source_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_world_event_stream_branch ON world_event_stream(branch_key, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_world_snapshots_parent ON world_snapshots(parent_snapshot_id)")
        seed_world_runtime_rules(conn)
        seed_world_action_rules(conn)
        seed_world_update_schedules(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO world_resource_accounts
            (account_key, owner_type, resource_type, balance)
            VALUES ('campus-services', 'system', 'money', 0)
            """
        )
        default_config = seed_default_environment_config(conn)
        now = datetime.now(WORLD_TZ).isoformat()
        budget_date = now[:10]
        conn.execute(
            """
            INSERT OR IGNORE INTO world_runtime
            (id, status, world_timezone, world_time, budget_date)
            VALUES (?, 'paused', ?, ?, ?)
            """,
            (WORLD_RUNTIME_ID, WORLD_TIMEZONE, now, budget_date),
        )
        conn.execute(
            """
            UPDATE world_runtime
            SET daily_auto_model_budget = 1000
            WHERE id = ? AND daily_auto_model_budget < 1000
            """,
            (WORLD_RUNTIME_ID,),
        )
        conn.execute(
            """
            UPDATE world_runtime
            SET environment_config_id = COALESCE(environment_config_id, ?),
                environment_version = CASE WHEN environment_version = '' THEN ? ELSE environment_version END,
                random_seed = CASE WHEN random_seed = '' THEN 'campus-default-seed-v1' ELSE random_seed END
            WHERE id = ?
            """,
            (default_config["id"], environment_version_label(default_config), WORLD_RUNTIME_ID),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO world_branches
            (branch_key, name, status, metadata_json)
            VALUES ('main', '主世界', 'active', '{}')
            """
        )
        conn.execute(
            "UPDATE world_event_stream SET branch_key = 'main' WHERE branch_key = ''"
        )
        seed_agent_personality_traits(conn)
        WORLD_SCHEMA_READY = True


def append_world_event(
    conn,
    event_type,
    title,
    content,
    tick_id=None,
    resident_id=None,
    location="",
    payload=None,
    day=None,
    slot=None,
    ensure_schema=True,
    source_type="runtime",
    source_id="",
    parent_event_id=None,
    root_event_id=None,
    rule_version="world-runtime-v1",
    occurred_at=None,
    branch_key=None,
):
    if ensure_schema:
        ensure_world_runtime_tables(conn)
    now = get_world_now()
    day = day or get_current_day(conn)
    slot = slot or world_slot_from_hour(now.hour)
    if parent_event_id:
        parent = conn.execute(
            "SELECT id, root_event_id, branch_key FROM world_event_stream WHERE id = ?",
            (parent_event_id,),
        ).fetchone()
        if not parent:
            raise ValueError("parent_event_id 不存在")
        root_event_id = root_event_id or parent["root_event_id"] or parent["id"]
        branch_key = branch_key or parent["branch_key"]
    if not branch_key:
        runtime_row = conn.execute(
            "SELECT active_branch_key FROM world_runtime WHERE id = ?",
            (WORLD_RUNTIME_ID,),
        ).fetchone()
        branch_key = runtime_row["active_branch_key"] if runtime_row else "main"
    cursor = conn.execute(
        """
        INSERT INTO world_event_stream
        (tick_id, day, slot, event_type, resident_id, location, title, content, payload,
         source_type, source_id, parent_event_id, root_event_id, rule_version, occurred_at,
         branch_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tick_id,
            day,
            slot,
            event_type,
            resident_id,
            location or "",
            title,
            content,
            json_dumps(payload or {}, ensure_ascii=False, default=_world_event_json_default),
            source_type or "runtime",
            str(source_id or ""),
            parent_event_id,
            root_event_id,
            rule_version or "world-runtime-v1",
            occurred_at or now.isoformat(),
            branch_key or "main",
        ),
    )
    event_id = cursor.lastrowid
    if not root_event_id:
        root_event_id = event_id
        conn.execute(
            "UPDATE world_event_stream SET root_event_id = ? WHERE id = ?",
            (event_id, event_id),
        )
    return dict(conn.execute("SELECT * FROM world_event_stream WHERE id = ?", (event_id,)).fetchone())
