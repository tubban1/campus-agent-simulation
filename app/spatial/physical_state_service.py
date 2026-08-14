"""Authoritative, local physical state for a spatial world.

This is intentionally a small derived layer: geometry remains in
``spatial_nodes`` / ``spatial_edges`` and this table stores observations that
change at runtime.  It gives the UI and Agent perception one common fact
source instead of reusing time-of-day crowd templates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional


def _execute(conn, statement: str, parameters=()):
    """Use named binds for SQLAlchemy and positional binds for runtime DBAPI."""
    if hasattr(conn, "exec_driver_sql"):
        from sqlalchemy import text
        parts = statement.split("?")
        if len(parts) - 1 != len(parameters):
            raise ValueError("SQL placeholder count does not match parameters")
        bound = parts[0]
        values = {}
        for index, tail in enumerate(parts[1:]):
            key = f"p{index}"
            bound += f":{key}{tail}"
            values[key] = parameters[index]
        return conn.execute(text(bound), values)
    return conn.execute(statement, parameters)


def _rows(result):
    if hasattr(result, "mappings"):
        return [dict(row) for row in result.mappings().all()]
    return [dict(row) for row in result.fetchall()]


def refresh_spatial_physical_states(
    conn,
    *,
    world_key: str,
    environment: Optional[dict[str, Any]] = None,
    observed_at: Optional[str] = None,
    node_ids: Optional[list[int]] = None,
) -> dict[str, int]:
    """Project factual world conditions onto nodes in *world_key*.

    Crowding is calculated from actual spatial presence, access is taken from
    the node/available service state, and weather is copied from the current
    observed environment.  No time-slot crowd coefficients are used here.
    """
    environment = environment or {}
    if observed_at is None:
        now = datetime.now(timezone.utc)
    elif isinstance(observed_at, datetime):
        now = observed_at
    else:
        # The world runtime supplies an ISO timestamp.  Physical illumination
        # must follow simulated campus time, never the host machine's clock.
        now = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    temperature = float(environment.get("temperature", 24) or 24)
    precipitation = float(environment.get("rainfall", 0) or 0)
    weather = str(environment.get("weather", "晴") or "晴")
    illumination = 1.0 if 7 <= now.hour < 19 else 0.25
    # One set-based upsert is important: the real campus has thousands of
    # imported nodes, so a Python loop would turn each environment refresh
    # into thousands of network round trips to PostgreSQL.
    storm_noise = 8.0 if weather in {"雷雨", "大雨"} else 0.0
    node_filter = ""
    node_params: list[Any] = []
    if node_ids:
        unique_ids = sorted({int(value) for value in node_ids if value is not None})
        if not unique_ids:
            return {"updated": 0, "skipped": 0}
        node_filter = " AND n.id IN (" + ", ".join("?" for _ in unique_ids) + ")"
        node_params = unique_ids
    result = _execute(conn, """
        INSERT INTO spatial_physical_states
          (world_key, node_id, temperature_c, precipitation, illumination,
           noise_db, crowd_density, air_quality, access_status, source,
           observed_at, expires_at, version)
        SELECT n.world_key, n.id, ?, ?, ?,
               28.0 +
                   CASE WHEN n.capacity > 0 AND COALESCE(o.occupancy, 0) >= n.capacity THEN 58.0
                        WHEN n.capacity > 0 THEN COALESCE(o.occupancy, 0) * 58.0 / n.capacity ELSE 0 END + ?,
               CASE WHEN n.capacity > 0 AND COALESCE(o.occupancy, 0) >= n.capacity THEN 1.0
                    WHEN n.capacity > 0 THEN COALESCE(o.occupancy, 0) * 1.0 / n.capacity ELSE 0 END,
               100.0,
               CASE WHEN n.status NOT IN ('open', '开放') OR COALESCE(r.resource_status, 'open') NOT IN ('open', '开放') THEN 'closed'
                    WHEN COALESCE(r.available_units, 1) <= 0 THEN 'restricted'
                    ELSE 'open' END,
               'runtime_observation', ?, ?, 1
        FROM spatial_nodes n
        LEFT JOIN (
            SELECT current_node_id, COUNT(*) AS occupancy
            FROM agent_spatial_states GROUP BY current_node_id
        ) o ON o.current_node_id = n.id
        LEFT JOIN (
            SELECT node_id, MIN(COALESCE(status, 'open')) AS resource_status,
                   MIN(COALESCE(available_units, 1)) AS available_units
            FROM spatial_resources GROUP BY node_id
        ) r ON r.node_id = n.id
        WHERE n.world_key = ?""" + node_filter + """
        ON CONFLICT(world_key, node_id) DO UPDATE SET
          temperature_c=excluded.temperature_c, precipitation=excluded.precipitation,
          illumination=excluded.illumination, noise_db=excluded.noise_db,
          crowd_density=excluded.crowd_density, air_quality=excluded.air_quality,
          access_status=CASE WHEN spatial_physical_states.source = 'map_event'
                                  AND spatial_physical_states.expires_at > excluded.observed_at
                             THEN spatial_physical_states.access_status ELSE excluded.access_status END,
          source=CASE WHEN spatial_physical_states.source = 'map_event'
                           AND spatial_physical_states.expires_at > excluded.observed_at
                      THEN spatial_physical_states.source ELSE excluded.source END,
          observed_at=excluded.observed_at,
          expires_at=CASE WHEN spatial_physical_states.source = 'map_event'
                               AND spatial_physical_states.expires_at > excluded.observed_at
                          THEN spatial_physical_states.expires_at ELSE excluded.expires_at END,
          version=spatial_physical_states.version + 1
        """, (temperature, precipitation, illumination, storm_noise, now, now, world_key, *node_params))
    return {"updated": max(0, int(getattr(result, "rowcount", 0) or 0)), "skipped": 0}


def apply_spatial_physical_event(
    conn, *, world_key: str, node_id: Optional[int] = None, edge_id: Optional[int] = None,
    access_status: str = "closed", duration_minutes: int = 60,
) -> dict[str, Any]:
    """Apply a traceable temporary closure/restriction from a real-world event."""
    if access_status not in {"open", "restricted", "closed"}:
        raise ValueError("access_status must be open, restricted or closed")
    if not node_id and not edge_id:
        raise ValueError("node_id or edge_id is required")
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=max(1, min(7 * 24 * 60, duration_minutes)))
    now_str = now.isoformat()
    expires_str = expires_at.isoformat()
    resolved_node_ids: list[int] = []
    if node_id:
        row = _execute(conn, "SELECT id FROM spatial_nodes WHERE id = ? AND world_key = ?", (node_id, world_key)).fetchone()
        if not row:
            raise ValueError("node does not belong to world_key")
        resolved_node_ids.append(int(row[0] if not isinstance(row, dict) else row["id"]))
    if edge_id:
        rows = _rows(_execute(conn, """
            SELECT edge.from_node_id, edge.to_node_id
            FROM spatial_edges edge JOIN spatial_nodes node ON node.id = edge.from_node_id
            WHERE edge.id = ? AND node.world_key = ?
        """, (edge_id, world_key)))
        if not rows:
            raise ValueError("edge does not belong to world_key")
        resolved_node_ids.extend([int(rows[0]["from_node_id"]), int(rows[0]["to_node_id"])])
        _execute(conn, """
            INSERT INTO spatial_edge_physical_states
              (world_key, edge_id, access_status, travel_factor, source, observed_at, expires_at, version)
            VALUES (?, ?, ?, ?, 'map_event', ?, ?, 1)
            ON CONFLICT(world_key, edge_id) DO UPDATE SET
              access_status=excluded.access_status, travel_factor=excluded.travel_factor,
              source=excluded.source, observed_at=excluded.observed_at,
              expires_at=excluded.expires_at, version=spatial_edge_physical_states.version + 1
        """, (world_key, edge_id, access_status, 1.8 if access_status == "restricted" else 1.0, now_str, expires_str))
    for current_node_id in sorted(set(resolved_node_ids)):
        _execute(conn, """
            INSERT INTO spatial_physical_states
              (world_key, node_id, precipitation, illumination, noise_db, crowd_density,
               air_quality, access_status, source, observed_at, expires_at, version)
            VALUES (?, ?, 0, 1, 30, 0, 100, ?, 'map_event', ?, ?, 1)
            ON CONFLICT(world_key, node_id) DO UPDATE SET
              access_status=excluded.access_status, source='map_event',
              observed_at=excluded.observed_at, expires_at=excluded.expires_at,
              version=spatial_physical_states.version + 1
        """, (world_key, current_node_id, access_status, now_str, expires_str))
    return {"world_key": world_key, "node_ids": sorted(set(resolved_node_ids)), "edge_id": edge_id,
            "access_status": access_status, "expires_at": expires_at.isoformat()}


def list_spatial_physical_states(conn, *, world_key: Optional[str] = None, node_ids: Optional[set[int]] = None) -> list[dict[str, Any]]:
    from app.db import db_savepoint
    try:
        with db_savepoint(conn, "list_physical_states"):
            query = "SELECT * FROM spatial_physical_states WHERE 1=1"
            params: list[Any] = []
            if world_key:
                query += " AND world_key = ?"
                params.append(world_key)
            if node_ids:
                placeholders = ", ".join("?" for _ in node_ids)
                query += f" AND node_id IN ({placeholders})"
                params.extend(sorted(node_ids))
            query += " ORDER BY node_id"
            return _rows(_execute(conn, query, params))
    except Exception:
        return []


def list_spatial_edge_physical_states(conn, *, world_key: Optional[str] = None) -> list[dict[str, Any]]:
    from app.db import db_savepoint
    try:
        with db_savepoint(conn, "list_edge_physical_states"):
            query = "SELECT * FROM spatial_edge_physical_states WHERE 1=1"
            params: list[Any] = []
            if world_key:
                query += " AND world_key = ?"
                params.append(world_key)
            query += " ORDER BY edge_id"
            return _rows(_execute(conn, query, params))
    except Exception:
        return []
