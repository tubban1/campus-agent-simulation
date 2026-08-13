from __future__ import annotations

import json
import hashlib
from app.json_utils import json_dumps
from datetime import datetime, timezone
from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


RULE_SEEDS = (
    (
        "space-official-hours",
        "空间开放时间与关闭状态",
        "institutional",
        {"closed_penalty_minor": 1200},
        {"monitoring_strength": 0.58},
    ),
    (
        "space-service-availability",
        "场所服务可用性",
        "service",
        {"service_requires_open": True},
        {"monitoring_strength": 0.15},
    ),
    (
        "space-capacity-pressure",
        "空间容量与拥挤",
        "capacity",
        {"queue_base_minutes": 8, "overload_harm_scale": 0.18},
        {"monitoring_strength": 0.32},
    ),
    (
        "space-boundary-enforcement",
        "空间边界监测与执法",
        "enforcement",
        {"sanction_minor": 1200},
        {"monitoring_strength": 0.58, "delayed_detection": True},
    ),
)


def _json(value) -> str:
    return json_dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


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


def constraint_runtime_available(conn):
    return _table_exists(conn, "constraint_evaluations")


def seed_constraint_runtime(conn):
    created = 0
    for rule_key, name, layer, parameters, enforcement in RULE_SEEDS:
        before = conn.execute(
            "SELECT id FROM constraint_rules WHERE rule_key = ?", (rule_key,)
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO constraint_rules
            (rule_key, name, constraint_layer, target_type, target_key,
             parameters_json, enforcement_json, status, version,
             created_by_type, created_by_id)
            VALUES (?, ?, ?, 'space', '*', ?, ?, 'active', 1, 'system', 'stage-3.1.1')
            """,
            (rule_key, name, layer, _json(parameters), _json(enforcement)),
        )
        if not before:
            created += 1
    return {"rules": len(RULE_SEEDS), "created": created}


def _runtime_position(conn):
    if not _table_exists(conn, "world_runtime"):
        return "main", 0
    row = conn.execute(
        "SELECT active_branch_key FROM world_runtime WHERE id = 1"
    ).fetchone()
    if not row:
        return "main", 0
    tick_number = 0
    if _table_exists(conn, "world_ticks"):
        tick = conn.execute(
            "SELECT COALESCE(MAX(tick_index), 0) AS value FROM world_ticks"
        ).fetchone()
        tick_number = int(tick["value"] or 0)
    return row["active_branch_key"] or "main", tick_number


def _profile(conn, resident_id):
    if not _table_exists(conn, "agent_capability_profiles"):
        return {}
    row = conn.execute(
        """
        SELECT physical_endurance, risk_tolerance, rule_adherence,
               social_capital, institutional_access, stress_resilience
        FROM agent_capability_profiles WHERE resident_id = ?
        """,
        (resident_id,),
    ).fetchone()
    return dict(row) if row else {}


def _rule_versions(conn):
    rows = conn.execute(
        """
        SELECT rule_key, version, parameters_json, enforcement_json
        FROM constraint_rules WHERE status = 'active'
        ORDER BY rule_key
        """
    ).fetchall()
    versions = {}
    configs = {}
    for row in rows:
        versions[row["rule_key"]] = int(row["version"])
        configs[row["rule_key"]] = {
            "parameters": _load(row["parameters_json"], {}),
            "enforcement": _load(row["enforcement_json"], {}),
        }
    return versions, configs


def _deterministic_draw(key, channel):
    digest = hashlib.sha256(f"{key}:{channel}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(0xFFFFFFFFFFFF)


def evaluate_space_constraint(
    conn,
    *,
    resident_id,
    target_node,
    world_time=None,
    action="enter",
    requested_response="auto",
    physically_possible=True,
):
    now = _now(world_time)
    location = target_node.get("properties", {}).get("location") or target_node["name"]
    space = conn.execute(
        """
        SELECT capacity, open_hour, close_hour, status
        FROM campus_spaces WHERE location = ?
        """,
        (location,),
    ).fetchone()
    branch_key, tick_number = _runtime_position(conn)
    profile = _profile(conn, resident_id)
    versions, configs = _rule_versions(conn)
    if not space:
        capacity = 0
        occupancy = 0
        within_hours = True
        officially_permitted = True
        service_available = True
    else:
        capacity = max(0, int(space["capacity"]))
        occupancy = int(
            conn.execute(
                """
                SELECT COUNT(*) AS value
                FROM agent_spatial_states
                WHERE current_node_id = ? AND resident_id != ?
                  AND movement_status NOT IN ('moving', 'replanning')
                """,
                (target_node["id"], resident_id),
            ).fetchone()["value"]
        )
        hour = now.hour
        open_hour = int(space["open_hour"])
        close_hour = int(space["close_hour"])
        within_hours = (
            open_hour <= hour < close_hour
            if close_hour != 24
            else hour >= open_hour
        )
        officially_permitted = space["status"] == "开放" and within_hours
        service_available = officially_permitted
    pressure = (
        round(occupancy / capacity, 4)
        if capacity > 0
        else (1.0 if occupancy or space else 0.0)
    )
    full = bool(space) and (capacity <= 0 or occupancy >= capacity)
    risk = int(profile.get("risk_tolerance", 50))
    adherence = int(profile.get("rule_adherence", 50))
    endurance = int(profile.get("physical_endurance", 50))
    social = int(profile.get("social_capital", 50))
    stress_resilience = int(profile.get("stress_resilience", 50))
    monitoring = float(
        configs.get("space-boundary-enforcement", {})
        .get("enforcement", {})
        .get("monitoring_strength", 0.58)
    )
    violation_score = risk + (100 - adherence) + endurance * 0.25 + social * 0.1
    if requested_response != "auto":
        selected = requested_response
    elif not physically_possible:
        selected = "blocked"
    elif officially_permitted and not full:
        selected = "enter"
    elif violation_score >= (112 if full else 105):
        selected = "bypass"
    elif full:
        selected = "queue"
    else:
        selected = "request_exception" if int(profile.get("institutional_access", 50)) >= 65 else "abandon"
    success_probability = _clamp(
        0.28 + endurance * 0.005 + risk * 0.002 - max(0.0, pressure - 1.0) * 0.18,
        0.05,
        0.95,
    )
    detection_probability = _clamp(
        monitoring + max(0, 50 - social) * 0.003 + max(0.0, pressure - 0.8) * 0.12,
        0.05,
        0.98,
    )
    harm_probability = _clamp(
        0.04 + max(0, 55 - endurance) * 0.004
        + max(0, 55 - stress_resilience) * 0.0015
        + max(0.0, pressure - 1.0) * 0.2,
        0.01,
        0.8,
    )
    expected_wait = round(
        max(0.0, pressure) * float(
            configs.get("space-capacity-pressure", {})
            .get("parameters", {})
            .get("queue_base_minutes", 8)
        ),
        2,
    )
    sanction = int(
        configs.get("space-boundary-enforcement", {})
        .get("parameters", {})
        .get("sanction_minor", 1200)
    )
    evaluation_key = (
        f"{branch_key}:{tick_number}:{resident_id}:space:{target_node['id']}:"
        f"{action}:{int(now.timestamp())}:{requested_response}"
    )
    evidence = {
        "location": location,
        "space_status": space["status"] if space else "unregulated",
        "within_hours": within_hours,
        "profile": profile,
        "violation_score": round(violation_score, 2),
        "requested_response": requested_response,
    }
    conn.execute(
        """
        INSERT OR IGNORE INTO constraint_evaluations
        (evaluation_key, branch_key, tick_number, resident_id, target_type,
         target_key, action, physically_possible, officially_permitted,
         service_available, occupancy, capacity, capacity_pressure,
         expected_time_minutes, expected_cost_minor, success_probability,
         detection_probability, harm_probability, expected_sanction_minor,
         selected_response, rule_versions_json, evidence_json, evaluated_at)
        VALUES (?, ?, ?, ?, 'space', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?,
                ?, ?, ?, ?)
        """,
        (
            evaluation_key,
            branch_key,
            tick_number,
            resident_id,
            str(target_node["id"]),
            action,
            int(bool(physically_possible)),
            int(officially_permitted),
            int(service_available),
            occupancy,
            capacity,
            pressure,
            expected_wait,
            success_probability,
            detection_probability,
            harm_probability,
            sanction,
            selected,
            _json(versions),
            _json(evidence),
            now.isoformat(),
        ),
    )
    row = conn.execute(
        "SELECT * FROM constraint_evaluations WHERE evaluation_key = ?",
        (evaluation_key,),
    ).fetchone()
    result = dict(row)
    result["rule_versions"] = _load(result.pop("rule_versions_json"), {})
    result["evidence"] = _load(result.pop("evidence_json"), {})
    result["location"] = location
    result["full"] = full
    return result


def _record_consequence(
    conn,
    attempt_id,
    consequence_type,
    resident_id,
    magnitude,
    unit,
    occurred_at,
    details=None,
):
    conn.execute(
        """
        INSERT INTO constraint_consequences
        (attempt_id, consequence_type, target_type, target_id, magnitude,
         unit, details_json, occurred_at)
        VALUES (?, ?, 'resident', ?, ?, ?, ?, ?)
        """,
        (
            attempt_id,
            consequence_type,
            str(resident_id),
            float(magnitude),
            unit,
            _json(details or {}),
            occurred_at,
        ),
    )


def resolve_boundary_attempt(conn, evaluation, world_time=None):
    now = _now(world_time)
    attempt_key = f"constraint-evaluation:{evaluation['id']}"
    existing = conn.execute(
        "SELECT * FROM boundary_attempts WHERE attempt_key = ?", (attempt_key,)
    ).fetchone()
    if existing:
        item = dict(existing)
        item["outcome"] = _load(item.pop("outcome_json"), {})
        item["admitted"] = bool(item["succeeded"]) and item["strategy"] in {"enter", "bypass"}
        return item
    strategy = evaluation["selected_response"]
    succeeded = strategy == "enter"
    detected = False
    harmed = False
    status = "succeeded" if succeeded else "pending"
    if strategy == "bypass":
        succeeded = _deterministic_draw(attempt_key, "success") <= float(
            evaluation["success_probability"]
        )
        detected = _deterministic_draw(attempt_key, "detection") <= float(
            evaluation["detection_probability"]
        )
        harmed = _deterministic_draw(attempt_key, "harm") <= float(
            evaluation["harm_probability"]
        )
        status = "succeeded" if succeeded else "failed"
    elif strategy in {"abandon", "blocked"}:
        status = "abandoned" if strategy == "abandon" else "failed"
    outcome = {
        "officially_permitted": bool(evaluation["officially_permitted"]),
        "service_available": bool(evaluation["service_available"]),
        "capacity_pressure": float(evaluation["capacity_pressure"]),
        "success_drawn": strategy == "bypass",
    }
    cursor = conn.execute(
        """
        INSERT INTO boundary_attempts
        (attempt_key, evaluation_id, resident_id, strategy, status,
         succeeded, detected, harmed, sanction_minor, started_at, resolved_at,
         outcome_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attempt_key,
            evaluation["id"],
            evaluation["resident_id"],
            strategy,
            status,
            int(succeeded),
            int(detected),
            int(harmed),
            int(evaluation["expected_sanction_minor"]) if detected else 0,
            now.isoformat(),
            now.isoformat() if status not in {"planned", "pending"} else "",
            _json(outcome),
        ),
    )
    attempt_id = int(cursor.lastrowid)
    if succeeded:
        _record_consequence(
            conn, attempt_id, "admission", evaluation["resident_id"], 1, "boolean",
            now.isoformat(), {"strategy": strategy}
        )
    if detected:
        _record_consequence(
            conn, attempt_id, "detection", evaluation["resident_id"], 1, "boolean",
            now.isoformat(), {"strategy": strategy}
        )
        if _table_exists(conn, "institutional_cases"):
            try:
                from app.social_institutions.service import submit_institutional_case

                case = submit_institutional_case(
                    conn,
                    case_key=f"boundary-attempt:{attempt_id}",
                    rule_key="restricted-access",
                    subject_resident_id=int(evaluation["resident_id"]),
                    world_time=now,
                    evidence={
                        "source_event_id": attempt_key,
                        "constraint_evaluation_id": int(evaluation["id"]),
                    },
                    requested_outcome="review_conduct",
                    bypass_attempted=True,
                )
                conn.execute(
                    "UPDATE boundary_attempts SET institutional_case_id = ? WHERE id = ?",
                    (case["id"], attempt_id),
                )
            except ValueError:
                pass
    if harmed and _table_exists(conn, "agent_body_states"):
        conn.execute(
            """
            UPDATE agent_body_states
            SET health = MAX(0, health - 4), stress = MIN(100, stress + 6),
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE resident_id = ?
            """,
            (evaluation["resident_id"],),
        )
        _record_consequence(
            conn, attempt_id, "injury", evaluation["resident_id"], 4, "health",
            now.isoformat(), {"strategy": strategy}
        )
    row = dict(
        conn.execute("SELECT * FROM boundary_attempts WHERE id = ?", (attempt_id,)).fetchone()
    )
    row["outcome"] = _load(row.pop("outcome_json"), {})
    row["admitted"] = bool(succeeded) and strategy in {"enter", "bypass"}
    return row


def list_constraint_evaluations(conn, resident_id=None, limit=100):
    if resident_id is None:
        rows = conn.execute(
            "SELECT * FROM constraint_evaluations ORDER BY id DESC LIMIT ?",
            (min(max(int(limit), 1), 500),),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM constraint_evaluations
            WHERE resident_id = ? ORDER BY id DESC LIMIT ?
            """,
            (resident_id, min(max(int(limit), 1), 500)),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["rule_versions"] = _load(item.pop("rule_versions_json"), {})
        item["evidence"] = _load(item.pop("evidence_json"), {})
        result.append(item)
    return result


def latest_explicit_constraint_response(conn, resident_id, target_key):
    if not constraint_runtime_available(conn):
        return "auto"
    row = conn.execute(
        """
        SELECT evidence_json FROM constraint_evaluations
        WHERE resident_id = ? AND target_type = 'space' AND target_key = ?
        ORDER BY id DESC LIMIT 1
        """,
        (resident_id, str(target_key)),
    ).fetchone()
    if not row:
        return "auto"
    requested = _load(row["evidence_json"], {}).get("requested_response", "auto")
    return requested if requested != "auto" else "auto"
