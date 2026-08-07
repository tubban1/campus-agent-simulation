from __future__ import annotations

from datetime import datetime, timezone


INTENSIVE_ACTIONS = {
    "attend_class",
    "club_activity",
    "collaborate",
    "conflict",
}


def _clamp(value):
    return round(max(0.0, min(100.0, float(value))), 3)


def _parsed_time(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def body_runtime_available(conn):
    return bool(conn.execute("PRAGMA table_info(agent_body_states)").fetchall())


def get_body_state(conn, resident_id):
    if not body_runtime_available(conn):
        return None
    row = conn.execute(
        "SELECT * FROM agent_body_states WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    return dict(row) if row else None


def body_action_checks(conn, resident_id, action):
    state = get_body_state(conn, resident_id)
    if not state:
        return []
    checks = []

    def add(key, passed, actual, required, code, reason):
        checks.append(
            {
                "key": key,
                "passed": bool(passed),
                "actual": actual,
                "required": required,
                "failure_code": "" if passed else code,
                "reason": "" if passed else reason,
            }
        )

    if action in INTENSIVE_ACTIONS:
        add(
            "body_fatigue",
            float(state["fatigue"]) < 88,
            state["fatigue"],
            "< 88",
            "too_fatigued",
            "疲劳过高，需要先休息",
        )
        add(
            "body_health",
            float(state["health"]) >= 35,
            state["health"],
            ">= 35",
            "health_too_low",
            "健康状态不足以执行高强度行动",
        )
    if action not in {"consume", "rest", "move"}:
        add(
            "body_hunger",
            float(state["hunger"]) < 94,
            state["hunger"],
            "< 94",
            "too_hungry",
            "饥饿过高，需要先补充食物",
        )
    if action in {"attend_class", "collaborate", "observe"}:
        add(
            "body_attention",
            float(state["attention"]) >= 15,
            state["attention"],
            ">= 15",
            "attention_depleted",
            "注意力不足，需要恢复后再继续",
        )
    return checks


ACTION_EFFECTS = {
    "consume": {"hunger": -38, "fatigue": -3, "health": 2},
    "rest": {"fatigue": -30, "sleep_debt": -18, "stress": -10, "attention": 16},
    "reflect": {"stress": -12, "attention": 8, "social_energy": 3},
    "chat": {"stress": -3, "social_energy": 9},
    "club_activity": {"fatigue": 10, "stress": -8, "health": 2},
    "conflict": {"fatigue": 6, "stress": 18, "social_energy": -10},
    "collaborate": {"fatigue": 4, "stress": 2, "social_energy": -5},
    "attend_class": {"fatigue": 7, "hunger": 4, "attention": -12},
    "move": {"fatigue": 3, "hunger": 1},
}


def apply_action_body_effects(conn, resident_id, action, success=True):
    state = get_body_state(conn, resident_id)
    if not state:
        return None
    effects = dict(ACTION_EFFECTS.get(action, {}))
    if action == "rest" and success and float(state["hunger"]) >= 90:
        effects["hunger"] = min(float(effects.get("hunger", 0)), -14)
        effects["health"] = float(effects.get("health", 0)) + 0.5
    ratio = 1.0 if success else 0.35
    updated = {}
    for field in (
        "hunger",
        "fatigue",
        "sleep_debt",
        "stress",
        "attention",
        "social_energy",
        "health",
        "weather_exposure",
    ):
        updated[field] = _clamp(
            float(state[field]) + float(effects.get(field, 0)) * ratio
        )
    conn.execute(
        """
        UPDATE agent_body_states
        SET hunger = ?, fatigue = ?, sleep_debt = ?, stress = ?,
            attention = ?, social_energy = ?, health = ?,
            weather_exposure = ?, version = version + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ?
        """,
        (
            updated["hunger"],
            updated["fatigue"],
            updated["sleep_debt"],
            updated["stress"],
            updated["attention"],
            updated["social_energy"],
            updated["health"],
            updated["weather_exposure"],
            resident_id,
        ),
    )
    energy = _summary_energy(updated)
    conn.execute(
        "UPDATE agent_profiles SET energy = ? WHERE resident_id = ?",
        (energy, resident_id),
    )
    return {
        "before": {field: state[field] for field in updated},
        "after": updated,
        "action": action,
        "energy": energy,
    }


def _summary_energy(state):
    burden = (
        float(state["fatigue"]) * 0.55
        + float(state["hunger"]) * 0.2
        + float(state["stress"]) * 0.1
        + float(state["sleep_debt"]) * 0.15
    )
    return int(round(max(0, min(100, 100 - burden))))


def advance_body_states(conn, world_time, tick_number, environment):
    if not body_runtime_available(conn):
        return []
    rows = conn.execute(
        """
        SELECT body.*, residents.location, spatial.movement_status
        FROM agent_body_states body
        JOIN residents ON residents.id = body.resident_id
        LEFT JOIN agent_spatial_states spatial
          ON spatial.resident_id = body.resident_id
        ORDER BY body.resident_id
        """
    ).fetchall()
    results = []
    rainfall = float(environment.get("rainfall") or 0)
    temperature = float(environment.get("temperature") or 24)
    exam_pressure = float(environment.get("exam_pressure") or 0)
    for raw in rows:
        state = dict(raw)
        previous_at = _parsed_time(state.get("last_updated_at"))
        if not previous_at:
            elapsed_hours = 0.0
        else:
            elapsed_hours = min(
                8.0,
                max(0.0, (world_time - previous_at).total_seconds() / 3600.0),
            )
        sleeping = state["location"] == "宿舍区" and (
            world_time.hour < 6 or world_time.hour >= 23
        )
        moving = state.get("movement_status") in {"moving", "replanning"}
        waiting = state.get("movement_status") == "waiting"
        hunger = float(state["hunger"]) + elapsed_hours * (0.45 if sleeping else 3.2)
        fatigue = float(state["fatigue"]) + elapsed_hours * (
            -8.0 if sleeping else (5.0 if moving else 2.0)
        )
        sleep_debt = float(state["sleep_debt"]) + elapsed_hours * (
            -10.0 if sleeping else 1.2
        )
        stress = float(state["stress"]) + elapsed_hours * (
            (-4.0 if sleeping else exam_pressure / 100.0)
            + (4.0 if waiting else 0.0)
        )
        attention = float(state["attention"]) + elapsed_hours * (
            7.0 if sleeping else -2.0 - max(0, fatigue - 70) / 20.0
        )
        social_energy = float(state["social_energy"]) + elapsed_hours * (
            3.0 if sleeping else (-1.5 if waiting else 0.0)
        )
        exposure_rate = 0.0
        if moving:
            exposure_rate = rainfall / 25.0 + max(0, abs(temperature - 22) - 8) / 5.0
        weather_exposure = float(state["weather_exposure"]) + elapsed_hours * exposure_rate
        health = float(state["health"])
        if hunger > 95:
            health -= elapsed_hours * (0.5 if sleeping else 2.0)
        if fatigue > 92 or weather_exposure > 80:
            health -= elapsed_hours * 2.0
        updated = {
            "hunger": _clamp(hunger),
            "fatigue": _clamp(fatigue),
            "sleep_debt": _clamp(sleep_debt),
            "stress": _clamp(stress),
            "attention": _clamp(attention),
            "social_energy": _clamp(social_energy),
            "health": _clamp(health),
            "weather_exposure": _clamp(weather_exposure),
        }
        conn.execute(
            """
            UPDATE agent_body_states
            SET hunger = ?, fatigue = ?, sleep_debt = ?, stress = ?,
                attention = ?, social_energy = ?, health = ?,
                weather_exposure = ?, last_updated_at = ?,
                last_updated_tick = ?, version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE resident_id = ?
            """,
            (
                updated["hunger"],
                updated["fatigue"],
                updated["sleep_debt"],
                updated["stress"],
                updated["attention"],
                updated["social_energy"],
                updated["health"],
                updated["weather_exposure"],
                world_time.isoformat(),
                tick_number,
                state["resident_id"],
            ),
        )
        energy = _summary_energy(updated)
        conn.execute(
            "UPDATE agent_profiles SET energy = ? WHERE resident_id = ?",
            (energy, state["resident_id"]),
        )
        results.append(
            {
                "resident_id": int(state["resident_id"]),
                "elapsed_hours": round(elapsed_hours, 4),
                **updated,
                "energy": energy,
                "sleeping": sleeping,
                "moving": moving,
                "waiting": waiting,
            }
        )
    return results
