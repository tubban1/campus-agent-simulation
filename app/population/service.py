from __future__ import annotations

import json
from app.json_utils import json_dumps
from datetime import datetime, timezone
from app.world_runtime.clock import parse_world_datetime, WORLD_TZ

from app.capability_runtime import (
    derive_capability_profile,
    derive_opportunities,
    spatial_capability_values,
)
from app.economy.service import seed_economy_foundation

DEPARTURE_EVENTS = {"graduation", "withdrawal", "city_migration"}
ROLE_EVENTS = {"transfer_program", "teacher_transfer", "job_change"}


def _json(value):
    return json_dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _time(value=None):
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


def population_runtime_available(conn):
    return _table_exists(conn, "population_profiles")


def _initialize_new_resident_runtime(
    conn, resident_id, role, money, location, energy
):
    if _table_exists(conn, "agent_capability_profiles"):
        capability = derive_capability_profile(resident_id, role, money, {})
        fields = (
            "physical_endurance",
            "time_management",
            "risk_tolerance",
            "rule_adherence",
            "information_literacy",
            "economic_access",
            "social_capital",
            "institutional_access",
            "language_access",
            "stress_resilience",
        )
        conn.execute(
            f"""
            INSERT OR IGNORE INTO agent_capability_profiles
            (resident_id, {", ".join(fields)}, source, source_detail,
             defaults_version, missing_value_policy, version)
            VALUES (?, {", ".join("?" for _ in fields)}, ?, ?, ?, ?, ?)
            """,
            (
                resident_id,
                *(capability[field] for field in fields),
                capability["source"],
                _json(capability["source_detail"]),
                capability["defaults_version"],
                capability["missing_value_policy"],
                capability["version"],
            ),
        )
        for opportunity in derive_opportunities(capability):
            conn.execute(
                """
                INSERT OR IGNORE INTO agent_opportunity_access
                (resident_id, opportunity_key, access_level,
                 time_cost_multiplier, monetary_barrier, eligibility,
                 source, source_detail, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resident_id,
                    opportunity["opportunity_key"],
                    opportunity["access_level"],
                    opportunity["time_cost_multiplier"],
                    opportunity["monetary_barrier"],
                    opportunity["eligibility"],
                    opportunity["source"],
                    _json(opportunity["source_detail"]),
                    opportunity["version"],
                ),
            )
    else:
        capability = None

    nodes = (
        conn.execute(
            "SELECT id, code, x, y, z, properties FROM spatial_nodes ORDER BY id"
        ).fetchall()
        if _table_exists(conn, "spatial_nodes")
        else []
    )
    target_node = None
    for node in nodes:
        properties = _load(node["properties"], {})
        if properties.get("location") == location:
            target_node = node
            break
    if not target_node and nodes:
        target_node = next(
            (node for node in nodes if node["code"] == "dorm"), nodes[0]
        )
    if target_node and _table_exists(conn, "agent_spatial_states"):
        branch = conn.execute(
            "SELECT active_branch_key FROM world_runtime WHERE id = 1"
        ).fetchone()
        branch_key = branch["active_branch_key"] if branch else "main"
        conn.execute(
            """
            INSERT OR IGNORE INTO agent_spatial_states
            (resident_id, current_node_id, x, y, z, facing_x, facing_z,
             movement_status, path, path_index, progress, updated_tick,
             version, branch_key)
            VALUES (?, ?, ?, ?, ?, 0, 1, 'idle', '[]', 0, 0, 0, 1, ?)
            """,
            (
                resident_id,
                target_node["id"],
                target_node["x"],
                target_node["y"],
                target_node["z"],
                branch_key or "main",
            ),
        )
    if capability and _table_exists(conn, "agent_spatial_capabilities"):
        spatial = spatial_capability_values(capability)
        conn.execute(
            """
            INSERT OR IGNORE INTO agent_spatial_capabilities
            (resident_id, base_speed_m_per_min, mobility_class,
             accessibility_needs, perception_radius_m, hearing_radius_m,
             source, version)
            VALUES (?, ?, 'standard', '{}', ?, ?,
                    'derived-capability-v1', 1)
            """,
            (
                resident_id,
                spatial["base_speed_m_per_min"],
                spatial["perception_radius_m"],
                spatial["hearing_radius_m"],
            ),
        )
    if _table_exists(conn, "agent_body_states"):
        conn.execute(
            """
            INSERT OR IGNORE INTO agent_body_states
            (resident_id, hunger, fatigue, sleep_debt, stress, attention,
             social_energy, health, weather_exposure, hydration, nutrition,
             activity_load, illness_load, last_updated_tick, source, version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 25, 78, 18, 0, 0, 'population-entry', 1)
            """,
            (
                resident_id,
                float(20 + resident_id % 16),
                float(max(0, 100 - energy)),
                float(8 + resident_id % 12),
                float(22 + resident_id % 18),
                float(max(35, energy)),
                float(50 + resident_id % 31),
                float(88 + resident_id % 10),
            ),
        )
    if _table_exists(conn, "economic_actors"):
        seed_economy_foundation(conn)
    if _table_exists(conn, "inventory"):
        from app.body_runtime import ensure_agent_dorm_inventory
        ensure_agent_dorm_inventory(conn, resident_id)


def seed_population_runtime(conn, now=None):
    now = _time(now).isoformat()
    residents = conn.execute(
        "SELECT id, role, location, created_at FROM residents ORDER BY id"
    ).fetchall()
    if _table_exists(conn, "inventory"):
        from app.body_runtime import ensure_agent_dorm_inventory
        for resident in residents:
            ensure_agent_dorm_inventory(conn, resident["id"])
    created = roles = residencies = 0
    for resident in residents:
        entered_at = resident["created_at"] or now
        before = conn.execute(
            "SELECT resident_id FROM population_profiles WHERE resident_id = ?",
            (resident["id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO population_profiles
            (resident_id, lifecycle_status, lifecycle_stage, origin_type,
             entry_reason, entered_at)
            VALUES (?, 'active', 'campus_member', 'existing',
                    'legacy_population_seed', ?)
            """,
            (resident["id"], entered_at),
        )
        created += int(before is None)
        existing_role = conn.execute(
            """
            SELECT id FROM resident_role_assignments
            WHERE resident_id = ? AND role_type = 'campus' AND status = 'active'
            """,
            (resident["id"],),
        ).fetchone()
        if not existing_role:
            conn.execute(
                """
                INSERT INTO resident_role_assignments
                (resident_id, role_type, role_key, starts_at, details_json)
                VALUES (?, 'campus', ?, ?, ?)
                """,
                (
                    resident["id"],
                    resident["role"],
                    entered_at,
                    _json({"source": "legacy_population_seed"}),
                ),
            )
            roles += 1
        existing_residency = conn.execute(
            """
            SELECT id FROM resident_residency_periods
            WHERE resident_id = ? AND status = 'active'
            """,
            (resident["id"],),
        ).fetchone()
        if not existing_residency:
            conn.execute(
                """
                INSERT INTO resident_residency_periods
                (resident_id, residence_type, location, starts_at, details_json)
                VALUES (?, 'campus', ?, ?, ?)
                """,
                (
                    resident["id"],
                    resident["location"],
                    entered_at,
                    _json({"source": "legacy_population_seed"}),
                ),
            )
            residencies += 1
    return {
        "profiles": len(residents),
        "created": created,
        "roles_created": roles,
        "residencies_created": residencies,
    }


def schedule_population_event(
    conn,
    *,
    event_key,
    event_type,
    effective_at,
    resident_id=None,
    payload=None,
    source_type="internal",
    source_id="",
    branch_key="main",
):
    existing = conn.execute(
        "SELECT * FROM population_events WHERE event_key = ?", (event_key,)
    ).fetchone()
    if existing:
        return dict(existing)
    cursor = conn.execute(
        """
        INSERT INTO population_events
        (event_key, event_type, resident_id, effective_at, source_type,
         source_id, branch_key, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_key,
            event_type,
            resident_id,
            _time(effective_at).isoformat(),
            source_type,
            source_id,
            branch_key,
            _json(payload or {}),
        ),
    )
    return dict(
        conn.execute(
            "SELECT * FROM population_events WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    )


def _record_effect(conn, event_id, index, effect_type, target_type, target_key, magnitude, now, details=None):
    conn.execute(
        """
        INSERT OR IGNORE INTO population_effects
        (effect_key, population_event_id, effect_type, target_type,
         target_key, magnitude, details_json, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"population:{event_id}:effect:{index}",
            event_id,
            effect_type,
            target_type,
            str(target_key),
            float(magnitude),
            _json(details or {}),
            now.isoformat(),
        ),
    )


def _create_resident(conn, event, payload, now):
    resident = payload.get("resident") or {}
    required = ("name", "role")
    if any(not resident.get(key) for key in required):
        raise ValueError("人口进入事件缺少 resident.name 或 resident.role")
    location = resident.get("location") or "宿舍区"
    cursor = conn.execute(
        """
        INSERT INTO residents
        (name, role, personality, goal, money, location)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            resident["name"],
            resident["role"],
            resident.get("personality") or "正在适应新环境",
            resident.get("goal") or "建立校园生活",
            int(resident.get("money", 100)),
            location,
        ),
    )
    resident_id = cursor.lastrowid
    conn.execute(
        """
        INSERT INTO agent_profiles
        (resident_id, gender, avatar_style, energy, mood, current_task,
         skills, strategy, schedule, perception)
        VALUES (?, ?, ?, ?, '平稳', '适应校园生活', '{}', '{}', '[]', '{}')
        """,
        (
            resident_id,
            resident.get("gender") or "未说明",
            resident.get("avatar_style") or "default",
            int(resident.get("energy", 80)),
        ),
    )
    _initialize_new_resident_runtime(
        conn,
        resident_id,
        resident["role"],
        int(resident.get("money", 100)),
        location,
        int(resident.get("energy", 80)),
    )
    stage = "exchange" if event["event_type"] == "exchange_arrival" else "student"
    conn.execute(
        """
        INSERT INTO population_profiles
        (resident_id, lifecycle_status, lifecycle_stage, origin_type,
         entry_reason, entered_at, expected_exit_at)
        VALUES (?, 'active', ?, ?, ?, ?, ?)
        """,
        (
            resident_id,
            stage,
            event["event_type"],
            payload.get("reason") or event["event_type"],
            now.isoformat(),
            payload.get("expected_exit_at") or "",
        ),
    )
    conn.execute(
        """
        INSERT INTO resident_role_assignments
        (resident_id, role_type, role_key, starts_at, source_event_id,
         details_json)
        VALUES (?, 'campus', ?, ?, ?, ?)
        """,
        (resident_id, resident["role"], now.isoformat(), event["id"], _json(payload)),
    )
    conn.execute(
        """
        INSERT INTO resident_residency_periods
        (resident_id, residence_type, location, starts_at, source_event_id,
         details_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            resident_id,
            payload.get("residence_type") or "campus",
            location,
            now.isoformat(),
            event["id"],
            _json(payload),
        ),
    )
    conn.execute(
        "UPDATE population_events SET resident_id = ? WHERE id = ?",
        (resident_id, event["id"]),
    )
    return resident_id, {"resident_id": resident_id, "lifecycle_status": "active"}


def _change_role(conn, event, payload, now):
    resident_id = event["resident_id"]
    role = payload.get("new_role")
    if not role:
        raise ValueError("角色变化事件缺少 new_role")
    previous = conn.execute(
        "SELECT role FROM residents WHERE id = ?", (resident_id,)
    ).fetchone()
    if not previous:
        raise ValueError("居民不存在")
    conn.execute(
        """
        UPDATE resident_role_assignments
        SET status = 'ended', ends_at = ?
        WHERE resident_id = ? AND role_type = 'campus' AND status = 'active'
        """,
        (now.isoformat(), resident_id),
    )
    conn.execute("UPDATE residents SET role = ? WHERE id = ?", (role, resident_id))
    conn.execute(
        """
        INSERT INTO resident_role_assignments
        (resident_id, role_type, role_key, starts_at, source_event_id,
         details_json)
        VALUES (?, 'campus', ?, ?, ?, ?)
        """,
        (resident_id, role, now.isoformat(), event["id"], _json(payload)),
    )
    return resident_id, {"role_before": previous["role"], "role_after": role}


def _depart(conn, event, payload, now):
    resident_id = event["resident_id"]
    profile = conn.execute(
        "SELECT * FROM population_profiles WHERE resident_id = ?", (resident_id,)
    ).fetchone()
    if not profile:
        raise ValueError("居民生命周期档案不存在")
    reason = payload.get("reason") or event["event_type"]
    conn.execute(
        """
        UPDATE population_profiles
        SET lifecycle_status = 'departed', lifecycle_stage = ?,
            exited_at = ?, exit_reason = ?, version = version + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ?
        """,
        (event["event_type"], now.isoformat(), reason, resident_id),
    )
    conn.execute(
        """
        UPDATE resident_role_assignments
        SET status = 'ended', ends_at = ?
        WHERE resident_id = ? AND status = 'active'
        """,
        (now.isoformat(), resident_id),
    )
    conn.execute(
        """
        UPDATE resident_residency_periods
        SET status = 'ended', ends_at = ?
        WHERE resident_id = ? AND status = 'active'
        """,
        (now.isoformat(), resident_id),
    )
    memberships = conn.execute(
        """
        SELECT organization_id, member_role FROM organization_members
        WHERE resident_id = ? AND status = 'active'
        """,
        (resident_id,),
    ).fetchall()
    for membership in memberships:
        conn.execute(
            """
            UPDATE organization_members SET status = 'inactive'
            WHERE organization_id = ? AND resident_id = ?
            """,
            (membership["organization_id"], resident_id),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO membership_transitions
            (transition_key, resident_id, organization_id, transition_type,
             role_before, source_event_id, occurred_at, details_json)
            VALUES (?, ?, ?, 'departure', ?, ?, ?, ?)
            """,
            (
                f"population:{event['id']}:departure:{membership['organization_id']}",
                resident_id,
                membership["organization_id"],
                membership["member_role"],
                event["id"],
                now.isoformat(),
                _json({"reason": reason}),
            ),
        )
    return resident_id, {
        "lifecycle_status": "departed",
        "memberships_ended": len(memberships),
    }


def _membership(conn, event, payload, now):
    resident_id = event["resident_id"]
    organization_id = int(payload.get("organization_id") or 0)
    if not organization_id:
        raise ValueError("组织变动事件缺少 organization_id")
    current = conn.execute(
        """
        SELECT member_role, status FROM organization_members
        WHERE organization_id = ? AND resident_id = ?
        """,
        (organization_id, resident_id),
    ).fetchone()
    role_before = current["member_role"] if current else ""
    role_after = payload.get("member_role") or "member"
    if event["event_type"] == "organization_leave":
        conn.execute(
            """
            UPDATE organization_members SET status = 'inactive'
            WHERE organization_id = ? AND resident_id = ?
            """,
            (organization_id, resident_id),
        )
        transition_type, role_after = "leave", ""
    else:
        conn.execute(
            """
            INSERT INTO organization_members
            (organization_id, resident_id, member_role, joined_day, status)
            VALUES (?, ?, ?, ?, 'active')
            ON CONFLICT (organization_id, resident_id)
            DO UPDATE SET member_role = excluded.member_role, status = 'active'
            """,
            (
                organization_id,
                resident_id,
                role_after,
                int(payload.get("joined_day", 1)),
            ),
        )
        transition_type = "role_change" if current else "join"
    conn.execute(
        """
        INSERT OR IGNORE INTO membership_transitions
        (transition_key, resident_id, organization_id, transition_type,
         role_before, role_after, source_event_id, occurred_at, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"population:{event['id']}:membership",
            resident_id,
            organization_id,
            transition_type,
            role_before,
            role_after,
            event["id"],
            now.isoformat(),
            _json(payload),
        ),
    )
    return resident_id, {
        "organization_id": organization_id,
        "transition_type": transition_type,
        "role_before": role_before,
        "role_after": role_after,
    }


def _move_residence(conn, event, payload, now):
    resident_id = event["resident_id"]
    location = payload.get("location")
    if not location:
        raise ValueError("宿舍调整事件缺少 location")
    previous = conn.execute(
        """
        SELECT location FROM resident_residency_periods
        WHERE resident_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1
        """,
        (resident_id,),
    ).fetchone()
    conn.execute(
        """
        UPDATE resident_residency_periods SET status = 'ended', ends_at = ?
        WHERE resident_id = ? AND status = 'active'
        """,
        (now.isoformat(), resident_id),
    )
    conn.execute(
        """
        INSERT INTO resident_residency_periods
        (resident_id, residence_type, location, starts_at, source_event_id,
         details_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            resident_id,
            payload.get("residence_type") or "campus",
            location,
            now.isoformat(),
            event["id"],
            _json(payload),
        ),
    )
    conn.execute("UPDATE residents SET location = ? WHERE id = ?", (location, resident_id))
    return resident_id, {
        "location_before": previous["location"] if previous else "",
        "location_after": location,
    }


def _apply_event(conn, event, now):
    payload = _load(event["payload_json"], {})
    if event["event_type"] in {"new_student", "exchange_arrival"}:
        resident_id, result = _create_resident(conn, event, payload, now)
    elif event["event_type"] in ROLE_EVENTS:
        resident_id, result = _change_role(conn, event, payload, now)
    elif event["event_type"] in DEPARTURE_EVENTS:
        resident_id, result = _depart(conn, event, payload, now)
    elif event["event_type"] == "leave_of_absence":
        resident_id = event["resident_id"]
        conn.execute(
            """
            UPDATE population_profiles
            SET lifecycle_status = 'leave', lifecycle_stage = 'leave',
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE resident_id = ?
            """,
            (resident_id,),
        )
        result = {"lifecycle_status": "leave"}
    elif event["event_type"] == "resume_study":
        resident_id = event["resident_id"]
        conn.execute(
            """
            UPDATE population_profiles
            SET lifecycle_status = 'active', lifecycle_stage = 'student',
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE resident_id = ?
            """,
            (resident_id,),
        )
        result = {"lifecycle_status": "active"}
    elif event["event_type"] in {
        "organization_join",
        "organization_leave",
        "leadership_change",
    }:
        resident_id, result = _membership(conn, event, payload, now)
    elif event["event_type"] == "dorm_move":
        resident_id, result = _move_residence(conn, event, payload, now)
    else:
        resident_id = event["resident_id"]
        result = {"recorded": True, "payload": payload}

    direction = -1 if event["event_type"] in DEPARTURE_EVENTS else 1
    _record_effect(
        conn,
        event["id"],
        0,
        "resource_demand",
        "campus",
        "population",
        direction,
        now,
        {"resident_id": resident_id, "event_type": event["event_type"]},
    )
    _record_effect(
        conn,
        event["id"],
        1,
        "relationship_network",
        "resident",
        resident_id or "pending",
        direction,
        now,
        {"history_preserved": True},
    )
    if event["event_type"] in {
        "organization_join",
        "organization_leave",
        "leadership_change",
    } or result.get("memberships_ended"):
        _record_effect(
            conn,
            event["id"],
            2,
            "organization_memory",
            "resident",
            resident_id,
            direction,
            now,
            result,
        )
    return result


def process_population_runtime(conn, world_time=None):
    if not population_runtime_available(conn):
        return {"available": False, "applied": [], "failed": []}
    now = _time(world_time)
    due = conn.execute(
        """
        SELECT * FROM population_events
        WHERE status = 'scheduled' AND effective_at <= ?
        ORDER BY effective_at, id
        """,
        (now.isoformat(),),
    ).fetchall()
    applied, failed = [], []
    for row in due:
        event = dict(row)
        try:
            result = _apply_event(conn, event, now)
            conn.execute(
                """
                UPDATE population_events
                SET status = 'applied', result_json = ?, applied_at = ?
                WHERE id = ?
                """,
                (_json(result), now.isoformat(), event["id"]),
            )
            applied.append(event["id"])
        except (ValueError, TypeError) as exc:
            conn.execute(
                """
                UPDATE population_events SET status = 'failed', result_json = ?
                WHERE id = ?
                """,
                (_json({"error": str(exc)}), event["id"]),
            )
            failed.append({"id": event["id"], "error": str(exc)})
    return {"available": True, "applied": applied, "failed": failed}


def get_resident_population_history(conn, resident_id):
    profile = conn.execute(
        "SELECT * FROM population_profiles WHERE resident_id = ?", (resident_id,)
    ).fetchone()
    if not profile:
        return None
    return {
        "profile": dict(profile),
        "events": [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM population_events
                WHERE resident_id = ? ORDER BY effective_at, id
                """,
                (resident_id,),
            ).fetchall()
        ],
        "roles": [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM resident_role_assignments
                WHERE resident_id = ? ORDER BY starts_at, id
                """,
                (resident_id,),
            ).fetchall()
        ],
        "residencies": [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM resident_residency_periods
                WHERE resident_id = ? ORDER BY starts_at, id
                """,
                (resident_id,),
            ).fetchall()
        ],
        "memberships": [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM membership_transitions
                WHERE resident_id = ? ORDER BY occurred_at, id
                """,
                (resident_id,),
            ).fetchall()
        ],
    }
