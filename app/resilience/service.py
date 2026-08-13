from __future__ import annotations

import json
import hashlib
from app.json_utils import json_dumps
from datetime import datetime, timedelta, timezone
from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


SHOCK_SEEDS = (
    ("heavy-rain", "暴雨", "weather", 180, [{"target_type": "campus", "target_key": "campus", "dimension": "travel_cost", "magnitude": 0.45, "unit": "multiplier"}]),
    ("power-outage", "停电", "power", 120, [{"target_type": "resource", "target_key": "*", "dimension": "resource_availability", "magnitude": -1, "unit": "status"}]),
    ("facility-closure", "设施故障关闭", "facility", 240, [{"target_type": "space", "target_key": "$scope", "dimension": "official_access", "magnitude": -1, "unit": "status"}]),
    ("supply-shortage", "供应短缺", "supply", 720, [{"target_type": "sector", "target_key": "$scope", "dimension": "supply", "magnitude": -0.4, "unit": "multiplier"}]),
    ("safety-incident", "安全事件", "safety", 240, [{"target_type": "space", "target_key": "$scope", "dimension": "health_risk", "magnitude": 0.5, "unit": "probability"}]),
    ("exam-delay", "考试延期", "exam", 1440, [{"target_type": "role", "target_key": "学生", "dimension": "policy", "magnitude": 1, "unit": "event"}]),
    ("public-health-event", "公共卫生事件", "public_health", 4320, [{"target_type": "campus", "target_key": "campus", "dimension": "health_risk", "magnitude": 0.35, "unit": "probability"}]),
    ("employment-shock", "就业机会冲击", "employment", 10080, [{"target_type": "role", "target_key": "学生", "dimension": "employment", "magnitude": -0.3, "unit": "multiplier"}]),
    ("price-shock", "价格冲击", "price", 1440, [{"target_type": "market", "target_key": "$scope", "dimension": "price", "magnitude": 0.25, "unit": "multiplier"}]),
    ("income-shock", "收入冲击", "income", 4320, [{"target_type": "role", "target_key": "$scope", "dimension": "income", "magnitude": -0.25, "unit": "multiplier"}]),
    ("policy-shock", "政策调整", "policy", 4320, [{"target_type": "campus", "target_key": "campus", "dimension": "policy", "magnitude": 1, "unit": "event"}]),
)


def _json(value):
    return json_dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _now(value=None):
    if value is None:
        return datetime.now(WORLD_TZ)
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    parsed = parse_world_datetime(value)
    if parsed:
        return parsed
    raise ValueError(f"无法解析的时间格式: {value}")


def _table_exists(conn, table_name):
    return bool(conn.execute(f"PRAGMA table_info({table_name})").fetchall())


def resilience_runtime_available(conn):
    return _table_exists(conn, "shock_instances")


def seed_resilience_runtime(conn):
    created = 0
    for key, name, shock_type, duration, impacts in SHOCK_SEEDS:
        before = conn.execute(
            "SELECT id FROM shock_definitions WHERE shock_key = ?", (key,)
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO shock_definitions
            (shock_key, name, shock_type, default_duration_minutes,
             impact_template_json, recovery_template_json,
             severity_range_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                name,
                shock_type,
                duration,
                _json(impacts),
                _json({"action_type": "repair", "effectiveness": 0.8}),
                _json({"min": 0.1, "max": 1.0}),
            ),
        )
        created += int(before is None)
    return {"definitions": len(SHOCK_SEEDS), "created": created}


def create_shock(
    conn,
    *,
    instance_key,
    shock_key,
    scheduled_at,
    severity,
    scope,
    parameters=None,
    source_type="internal",
    source_id="",
    branch_key="main",
    random_seed=0,
    duration_minutes=None,
    replay_of_instance_id=None,
):
    existing = conn.execute(
        "SELECT * FROM shock_instances WHERE instance_key = ?", (instance_key,)
    ).fetchone()
    if existing:
        return dict(existing)
    definition = conn.execute(
        "SELECT * FROM shock_definitions WHERE shock_key = ? AND status = 'active'",
        (shock_key,),
    ).fetchone()
    if not definition:
        raise ValueError("冲击定义不存在或不可用")
    severity = float(severity)
    if severity < 0 or severity > 1:
        raise ValueError("冲击严重度必须在 0 到 1 之间")
    start = _now(scheduled_at)
    duration = int(duration_minutes or definition["default_duration_minutes"])
    cursor = conn.execute(
        """
        INSERT INTO shock_instances
        (instance_key, definition_id, source_type, source_id, branch_key,
         random_seed, severity, scope_json, parameters_json, scheduled_at,
         expected_end_at, replay_of_instance_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            instance_key,
            definition["id"],
            source_type,
            source_id,
            branch_key,
            int(random_seed),
            severity,
            _json(scope),
            _json(parameters or {}),
            start.isoformat(),
            (start + timedelta(minutes=duration)).isoformat(),
            replay_of_instance_id,
        ),
    )
    return dict(
        conn.execute(
            "SELECT * FROM shock_instances WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    )


def replay_shock(conn, instance_id, instance_key, scheduled_at):
    original = conn.execute(
        """
        SELECT instance.*, definition.shock_key
        FROM shock_instances instance
        JOIN shock_definitions definition ON definition.id = instance.definition_id
        WHERE instance.id = ?
        """,
        (instance_id,),
    ).fetchone()
    if not original:
        raise ValueError("原始冲击不存在")
    start = _now(original["scheduled_at"])
    end = _now(original["expected_end_at"])
    duration = int((end - start).total_seconds() / 60)
    return create_shock(
        conn,
        instance_key=instance_key,
        shock_key=original["shock_key"],
        scheduled_at=scheduled_at,
        severity=original["severity"],
        scope=_load(original["scope_json"], {}),
        parameters=_load(original["parameters_json"], {}),
        source_type="replay",
        source_id=str(instance_id),
        branch_key=original["branch_key"],
        random_seed=original["random_seed"],
        duration_minutes=duration,
        replay_of_instance_id=instance_id,
    )


def _transition(conn, instance_id, from_status, to_status, trigger, now, details=None):
    conn.execute(
        """
        INSERT INTO shock_state_transitions
        (shock_instance_id, from_status, to_status, trigger_type,
         details_json, transitioned_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (instance_id, from_status, to_status, trigger, _json(details or {}), now.isoformat()),
    )
    fields = {
        "active": ("started_at", now.isoformat()),
        "recovering": ("recovery_started_at", now.isoformat()),
        "resolved": ("resolved_at", now.isoformat()),
    }
    extra = ""
    params = [to_status]
    if to_status in fields:
        column, value = fields[to_status]
        extra = f", {column} = ?"
        params.append(value)
    params.append(instance_id)
    conn.execute(
        f"UPDATE shock_instances SET status = ?{extra} WHERE id = ?",
        params,
    )


def _materialize_impacts(conn, instance, definition):
    scope = _load(instance["scope_json"], {})
    templates = _load(definition["impact_template_json"], [])
    impacts = []
    for index, template in enumerate(templates):
        target_key = template["target_key"]
        if target_key == "$scope":
            target_key = (
                scope.get("space")
                or scope.get("sector")
                or scope.get("role")
                or scope.get("target")
                or "campus"
            )
        magnitude = float(template["magnitude"]) * float(instance["severity"])
        key = f"shock:{instance['id']}:impact:{index}"
        conn.execute(
            """
            INSERT OR IGNORE INTO shock_impacts
            (impact_key, shock_instance_id, target_type, target_key,
             dimension, magnitude, unit, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                instance["id"],
                template["target_type"],
                target_key,
                template["dimension"],
                magnitude,
                template["unit"],
                _json({"template_index": index}),
            ),
        )
        impacts.append(
            conn.execute(
                "SELECT * FROM shock_impacts WHERE impact_key = ?", (key,)
            ).fetchone()
        )
    return impacts


def _apply_impact(conn, impact, now):
    previous = {}
    applied = {}
    if impact["target_type"] == "space" and impact["dimension"] == "official_access":
        row = conn.execute(
            "SELECT status FROM campus_spaces WHERE location = ?",
            (impact["target_key"],),
        ).fetchone()
        if row:
            previous = {"status": row["status"]}
            applied = {"status": "临时关闭"}
            conn.execute(
                "UPDATE campus_spaces SET status = '临时关闭' WHERE location = ?",
                (impact["target_key"],),
            )
    elif impact["target_type"] == "resource" and impact["dimension"] == "resource_availability":
        rows = conn.execute(
            "SELECT id, status FROM spatial_resources ORDER BY id"
        ).fetchall()
        previous = {"resources": {str(row["id"]): row["status"] for row in rows}}
        applied = {"status": "unavailable"}
        conn.execute("UPDATE spatial_resources SET status = 'unavailable'")
    elif impact["target_type"] == "campus" and impact["dimension"] == "travel_cost":
        rows = conn.execute(
            "SELECT id, weather_factor FROM spatial_edges ORDER BY id"
        ).fetchall()
        previous = {"edges": {str(row["id"]): row["weather_factor"] for row in rows}}
        factor = round(1 + abs(float(impact["magnitude"])), 4)
        applied = {"weather_factor_multiplier": factor}
        conn.execute(
            "UPDATE spatial_edges SET weather_factor = weather_factor * ?",
            (factor,),
        )
    else:
        applied = {"modifier_only": True, "magnitude": impact["magnitude"]}
    conn.execute(
        """
        UPDATE shock_impacts
        SET status = 'active', previous_state_json = ?,
            applied_state_json = ?, applied_at = ?
        WHERE id = ?
        """,
        (_json(previous), _json(applied), now.isoformat(), impact["id"]),
    )


def _resident_exposures(conn, instance, impact, now):
    scope = _load(instance["scope_json"], {})
    params = []
    where = ""
    if impact["target_type"] == "space":
        where = "WHERE resident.location = ?"
        params.append(impact["target_key"])
    elif impact["target_type"] == "role":
        where = "WHERE resident.role LIKE ?"
        params.append(f"%{impact['target_key']}%")
    residents = conn.execute(
        f"""
        SELECT resident.id, resident.role, resident.money,
               capability.stress_resilience, capability.economic_access,
               capability.social_capital
        FROM residents resident
        LEFT JOIN agent_capability_profiles capability
          ON capability.resident_id = resident.id
        {where}
        ORDER BY resident.id
        """,
        params,
    ).fetchall()
    for resident in residents:
        resilience = int(resident["stress_resilience"] or 50)
        economic = int(resident["economic_access"] or 50)
        social = int(resident["social_capital"] or 50)
        vulnerability = max(
            0.05,
            min(0.95, 0.75 - resilience * 0.004 - economic * 0.002 - social * 0.001),
        )
        coping = max(0.05, min(0.95, (resilience + economic + social) / 300))
        exposure = min(1.0, float(instance["severity"]) * (1.0 if where else 0.65))
        consequence = round(exposure * vulnerability * (1.2 - coping), 4)
        conn.execute(
            """
            INSERT OR IGNORE INTO resident_shock_exposures
            (exposure_key, shock_instance_id, impact_id, resident_id,
             exposure_level, vulnerability, coping_capacity,
             consequence_score, consequence_json, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"shock:{instance['id']}:impact:{impact['id']}:resident:{resident['id']}",
                instance["id"],
                impact["id"],
                resident["id"],
                exposure,
                vulnerability,
                coping,
                consequence,
                _json({"role": resident["role"], "money": resident["money"], "scope": scope}),
                now.isoformat(),
            ),
        )


def _revert_impact(conn, impact, now):
    previous = _load(impact["previous_state_json"], {})
    if impact["target_type"] == "space" and "status" in previous:
        conn.execute(
            "UPDATE campus_spaces SET status = ? WHERE location = ?",
            (previous["status"], impact["target_key"]),
        )
    elif impact["target_type"] == "resource" and "resources" in previous:
        for resource_id, status in previous["resources"].items():
            conn.execute(
                "UPDATE spatial_resources SET status = ? WHERE id = ?",
                (status, int(resource_id)),
            )
    elif impact["target_type"] == "campus" and "edges" in previous:
        for edge_id, factor in previous["edges"].items():
            conn.execute(
                "UPDATE spatial_edges SET weather_factor = ? WHERE id = ?",
                (factor, int(edge_id)),
            )
    conn.execute(
        """
        UPDATE shock_impacts SET status = 'reverted', reverted_at = ?
        WHERE id = ?
        """,
        (now.isoformat(), impact["id"]),
    )


def process_resilience_runtime(conn, world_time=None):
    if not resilience_runtime_available(conn):
        return {"available": False}
    now = _now(world_time)
    activated = []
    recovering = []
    resolved = []
    scheduled = conn.execute(
        """
        SELECT instance.*, definition.impact_template_json
        FROM shock_instances instance
        JOIN shock_definitions definition ON definition.id = instance.definition_id
        WHERE instance.status = 'scheduled' AND instance.scheduled_at <= ?
        ORDER BY instance.id
        """,
        (now.isoformat(),),
    ).fetchall()
    for instance in scheduled:
        _transition(conn, instance["id"], "scheduled", "active", "scheduled_time", now)
        impacts = _materialize_impacts(conn, instance, instance)
        for impact in impacts:
            _apply_impact(conn, impact, now)
            active_impact = conn.execute(
                "SELECT * FROM shock_impacts WHERE id = ?", (impact["id"],)
            ).fetchone()
            _resident_exposures(conn, instance, active_impact, now)
        activated.append(int(instance["id"]))
    due = conn.execute(
        """
        SELECT * FROM shock_instances
        WHERE status = 'active' AND expected_end_at <= ?
        ORDER BY id
        """,
        (now.isoformat(),),
    ).fetchall()
    for instance in due:
        _transition(conn, instance["id"], "active", "recovering", "duration_elapsed", now)
        conn.execute(
            """
            INSERT OR IGNORE INTO recovery_actions
            (action_key, shock_instance_id, action_type, target_type,
             target_key, effectiveness, status, planned_at, started_at)
            VALUES (?, ?, 'repair', 'campus', 'campus', 0.8, 'active', ?, ?)
            """,
            (f"shock:{instance['id']}:automatic-recovery", instance["id"], now.isoformat(), now.isoformat()),
        )
        recovering.append(int(instance["id"]))
    active_recovery = conn.execute(
        """
        SELECT * FROM shock_instances WHERE status = 'recovering' ORDER BY id
        """
    ).fetchall()
    for instance in active_recovery:
        impacts = conn.execute(
            """
            SELECT * FROM shock_impacts
            WHERE shock_instance_id = ? AND status = 'active'
            ORDER BY id
            """,
            (instance["id"],),
        ).fetchall()
        for impact in impacts:
            _revert_impact(conn, impact, now)
        conn.execute(
            """
            UPDATE recovery_actions
            SET status = 'completed', completed_at = ?
            WHERE shock_instance_id = ? AND status = 'active'
            """,
            (now.isoformat(), instance["id"]),
        )
        _transition(conn, instance["id"], "recovering", "resolved", "recovery_complete", now)
        resolved.append(int(instance["id"]))
    return {
        "available": True,
        "activated": activated,
        "recovering": recovering,
        "resolved": resolved,
    }
