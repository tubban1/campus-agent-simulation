from __future__ import annotations

from datetime import datetime, timezone
import json


INTENSIVE_ACTIONS = {
    "attend_class",
    "club_activity",
    "collaborate",
    "conflict",
}


from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


def _clamp(value):
    return round(max(0.0, min(100.0, float(value))), 3)


DAILY_NEED_DEFAULTS = {
    "hydration": 25.0,      # 0 = hydrated; 100 = acute dehydration
    "nutrition": 78.0,     # 0 = poor nutritional reserve; 100 = adequate
    "activity_load": 18.0, # rolling physical exertion load
    "illness_load": 0.0,   # symptom burden, not a medical diagnosis
}


def _body_value(state, field, default=0.0):
    value = state.get(field, default)
    return float(default if value is None else value)


def _parsed_time(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    return parse_world_datetime(value)


def _is_residential_sleep_space(location, node_type=None, node_properties=None):
    """Recognise both legacy zones and imported real-world residence nodes."""
    properties = node_properties
    if isinstance(properties, str):
        try:
            properties = json.loads(properties)
        except (TypeError, ValueError):
            properties = {}
    properties = properties if isinstance(properties, dict) else {}
    tags = properties.get("osm_tags")
    tags = tags if isinstance(tags, dict) else {}
    building = str(tags.get("building") or properties.get("building") or "").lower()
    location_text = str(location or "").lower()
    return (
        location_text in {"宿舍区", "宿舍", "公寓"}
        or any(token in location_text for token in ("宿舍", "公寓", "住宅", "residence"))
        or building in {"apartments", "dormitory", "residential", "residence"}
        or str(node_type or "").lower() in {"dormitory", "residence"}
    )


def _is_indoor_space(location, node_type=None, node_properties=None):
    """Use imported building metadata before falling back to legacy names."""
    properties = node_properties
    if isinstance(properties, str):
        try:
            properties = json.loads(properties)
        except (TypeError, ValueError):
            properties = {}
    properties = properties if isinstance(properties, dict) else {}
    tags = properties.get("osm_tags")
    tags = tags if isinstance(tags, dict) else {}
    if str(node_type or "").lower() == "building" or tags.get("building"):
        return True
    return str(location or "") in {"宿舍区", "教学楼", "图书馆", "食堂", "综合楼"}


def _night_sleep_state(state, world_time, tick_number, moving, waiting):
    """Classify night physiology without turning it into a cognitive action."""
    hour = world_time.hour
    if not (0 <= hour < 6):
        return "awake"
    role = str(state.get("role") or "")
    if moving or waiting or not _is_residential_sleep_space(
        state.get("location"), state.get("current_node_type"), state.get("current_node_properties")
    ):
        return "night_shift" if any(token in role for token in ("保安", "夜班", "值班")) else "night_activity"
    if float(state.get("health") or 100) < 55 or float(state.get("stress") or 0) >= 80:
        return "insomnia_discomfort"
    # A deterministic small share of sleep ticks are lighter sleep or a brief
    # awakening; it remains reproducible for replays and does not use a model.
    phase = (int(state["resident_id"]) * 17 + int(tick_number) * 11) % 100
    if phase < 12 or float(state.get("stress") or 0) >= 60:
        return "light_sleep"
    return "deep_sleep"


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
    if action not in {"consume", "rest", "move", "hydrate", "observe"}:
        add(
            "body_hunger",
            float(state["hunger"]) < 94,
            state["hunger"],
            "< 94",
            "too_hungry",
            "饥饿过高，需要先补充食物",
        )
    if action in {"attend_class", "collaborate"}:
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
    # Recovery must outpace ordinary deterioration.  Otherwise a resident
    # who has reached a canteen or bed remains permanently near zero health.
    # ``consume`` is refined in ``_action_effects_for_state`` below.  This
    # is the ordinary full-meal baseline; an acutely hungry resident gets a
    # larger, still bounded recovery instead of needing several fictional
    # meals across different ticks.
    "consume": {"hunger": -45, "hydration": -14, "nutrition": 24, "fatigue": -4, "stress": -3, "attention": 15, "health": 3, "activity_load": -5},
    "hydrate": {"hydration": -42, "stress": -3, "attention": 22},
    "rest": {"fatigue": -30, "sleep_debt": -18, "stress": -10, "attention": 25, "health": 12, "activity_load": -26, "illness_load": -4},
    "observe": {"attention": 12, "stress": -3},
    "reflect": {"stress": -12, "attention": 15, "social_energy": 3},
    "chat": {"stress": -3, "social_energy": 9},
    "club_activity": {"fatigue": 10, "stress": -8, "health": 2, "hydration": 6, "activity_load": 15},
    "conflict": {"fatigue": 6, "stress": 18, "social_energy": -10, "activity_load": 8},
    "collaborate": {"fatigue": 4, "stress": 2, "social_energy": -5, "activity_load": 5},
    "attend_class": {"fatigue": 7, "hunger": 4, "attention": -12, "hydration": 3, "activity_load": 4},
    "move": {"fatigue": 3, "hunger": 1, "hydration": 3, "activity_load": 8},
}


def _action_effects_for_state(action, state):
    """Return bodily effects for one completed action.

    Hunger is a *need* (0=satiated, 100=acute), rather than a calorie counter.
    A real meal therefore has a larger effect when it treats acute hunger, but
    is deliberately smaller when it is an ordinary snack.  This preserves a
    plausible daily rhythm without allowing an Agent to recover indefinitely
    by repeatedly clicking a canteen action.
    """
    effects = dict(ACTION_EFFECTS.get(action, {}))
    if action != "consume":
        return effects
    hunger = float(state.get("hunger") or 0)
    if hunger >= 85:
        effects["hunger"] = -62
        effects["health"] = 5
        effects["attention"] = 10
    elif hunger >= 60:
        effects["hunger"] = -50
        effects["health"] = 4
        effects["attention"] = 8
    elif hunger < 30:
        # A snack should not erase a full meal's worth of future need.
        effects["hunger"] = -18
        effects["health"] = 1
        effects["attention"] = 3
    if _body_value(state, "nutrition", DAILY_NEED_DEFAULTS["nutrition"]) < 40:
        effects["nutrition"] = 34
        effects["health"] = float(effects.get("health", 0)) + 2
    if _body_value(state, "hydration", DAILY_NEED_DEFAULTS["hydration"]) >= 70:
        effects["hydration"] = -24
    return effects


def apply_action_body_effects(conn, resident_id, action, success=True, recovery_quality=None):
    state = get_body_state(conn, resident_id)
    if not state:
        return None
    effects = _action_effects_for_state(action, state)
    if action == "consume" and recovery_quality is not None:
        # Quality is a 0-100 service/inventory signal.  It affects nutritional
        # reserve and health recovery, not whether food magically satisfies
        # immediate hunger; a low-quality but available meal still feeds a
        # hungry person, just less well over the longer term.
        quality_factor = max(0.45, min(1.1, float(recovery_quality) / 80.0))
        effects["nutrition"] = float(effects.get("nutrition", 0)) * quality_factor
        effects["health"] = float(effects.get("health", 0)) * quality_factor
    if action == "rest" and success and float(state["hunger"]) >= 90:
        effects["hunger"] = min(float(effects.get("hunger", 0)), -14)
        effects["health"] = float(effects.get("health", 0)) + 0.5
    ratio = 1.0 if success else 0.35
    body_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(agent_body_states)").fetchall()
    }
    fields = [
        "hunger",
        "fatigue",
        "sleep_debt",
        "stress",
        "attention",
        "social_energy",
        "health",
        "weather_exposure",
    ] + [field for field in DAILY_NEED_DEFAULTS if field in body_columns]
    updated = {}
    for field in fields:
        updated[field] = _clamp(
            _body_value(state, field, DAILY_NEED_DEFAULTS.get(field, 0.0))
            + float(effects.get(field, 0)) * ratio
        )
    assignments = ", ".join(f"{field} = ?" for field in fields)
    conn.execute(
        f"""UPDATE agent_body_states
            SET {assignments}, version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE resident_id = ?""",
        [updated[field] for field in fields] + [resident_id],
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
        _body_value(state, "fatigue") * 0.45
        + _body_value(state, "hunger") * 0.18
        + _body_value(state, "stress") * 0.08
        + _body_value(state, "sleep_debt") * 0.12
        + _body_value(state, "hydration", DAILY_NEED_DEFAULTS["hydration"]) * 0.08
        + (100 - _body_value(state, "nutrition", DAILY_NEED_DEFAULTS["nutrition"])) * 0.05
        + _body_value(state, "activity_load", DAILY_NEED_DEFAULTS["activity_load"]) * 0.04
        + _body_value(state, "illness_load", DAILY_NEED_DEFAULTS["illness_load"]) * 0.12
    )
    return int(round(max(0, min(100, 100 - burden))))


def advance_body_states(conn, world_time, tick_number, environment):
    if not body_runtime_available(conn):
        return []
    body_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(agent_body_states)").fetchall()
    }
    spatial_state_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(agent_spatial_states)").fetchall()
    }
    has_spatial_nodes = (
        "current_node_id" in spatial_state_columns
        and bool(conn.execute("PRAGMA table_info(spatial_nodes)").fetchall())
    )
    node_join = (
        "LEFT JOIN spatial_nodes node ON node.id = spatial.current_node_id"
        if has_spatial_nodes else ""
    )
    node_columns = (
        ", node.node_type AS current_node_type, node.properties AS current_node_properties"
        if has_spatial_nodes else ", NULL AS current_node_type, NULL AS current_node_properties"
    )
    rows = conn.execute(
        f"""
        SELECT body.*, residents.location, residents.role, spatial.movement_status
               {node_columns}
        FROM agent_body_states body
        JOIN residents ON residents.id = body.resident_id
        LEFT JOIN agent_spatial_states spatial
          ON spatial.resident_id = body.resident_id
        {node_join}
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
        moving = state.get("movement_status") in {"moving", "replanning"}
        waiting = state.get("movement_status") == "waiting"
        sleep_state = _night_sleep_state(state, world_time, tick_number, moving, waiting)
        sleeping = sleep_state in {"deep_sleep", "light_sleep"}
        rates = {
            "deep_sleep": {"hunger": 0.25, "fatigue": -10.0, "sleep_debt": -12.0, "stress": -5.0, "attention": 9.0, "social_energy": 4.0, "hydration": 0.25, "nutrition": -0.12, "activity_load": -8.0, "illness_load": -1.5},
            "light_sleep": {"hunger": 0.4, "fatigue": -5.0, "sleep_debt": -6.0, "stress": -2.0, "attention": 4.0, "social_energy": 2.0, "hydration": 0.32, "nutrition": -0.12, "activity_load": -4.0, "illness_load": -0.6},
            "insomnia_discomfort": {"hunger": 1.0, "fatigue": 1.4, "sleep_debt": 1.8, "stress": 2.2, "attention": -2.5, "social_energy": -0.5, "hydration": 0.9, "nutrition": -0.25, "activity_load": -0.5, "illness_load": 0.6},
            "night_activity": {"hunger": 2.5, "fatigue": 4.0 if moving else 2.4, "sleep_debt": 1.4, "stress": exam_pressure / 100.0 + (4.0 if waiting else 0.0), "attention": -2.0, "social_energy": -1.5 if waiting else 0.0, "hydration": 2.0 if moving else 1.1, "nutrition": -0.35, "activity_load": 5.0 if moving else 1.5, "illness_load": 0.1},
            "night_shift": {"hunger": 2.0, "fatigue": 3.0, "sleep_debt": 1.1, "stress": exam_pressure / 100.0, "attention": -1.5, "social_energy": -0.5, "hydration": 1.6, "nutrition": -0.3, "activity_load": 3.0, "illness_load": 0.08},
            # The old 3.2/h awake rate produced more than 75 hunger points
            # per calendar day before any walking costs.  It forced every
            # resident into an endless canteen loop even after a full meal.
            # This is a lightweight BMR/activity approximation: resting
            # awake life creates a modest need, while walking adds the
            # activity load through the moving branch.
            "awake": {"hunger": 2.0 if moving else 1.15, "fatigue": 3.2 if moving else 1.35, "sleep_debt": 0.8, "stress": exam_pressure / 100.0 + (3.0 if waiting else 0.0), "attention": -1.5 - max(0, float(state["fatigue"]) - 70) / 25.0, "social_energy": -1.0 if waiting else 0.0, "hydration": 2.1 if moving else 1.05, "nutrition": -0.32, "activity_load": 4.5 if moving else -0.4, "illness_load": 0.0},
        }[sleep_state]
        hunger = float(state["hunger"]) + elapsed_hours * rates["hunger"]
        fatigue = float(state["fatigue"]) + elapsed_hours * rates["fatigue"]
        sleep_debt = float(state["sleep_debt"]) + elapsed_hours * rates["sleep_debt"]
        stress = float(state["stress"]) + elapsed_hours * rates["stress"]
        attention = float(state["attention"]) + elapsed_hours * rates["attention"]
        social_energy = float(state["social_energy"]) + elapsed_hours * rates["social_energy"]
        hydration = _body_value(state, "hydration", DAILY_NEED_DEFAULTS["hydration"]) + elapsed_hours * rates["hydration"]
        nutrition = _body_value(state, "nutrition", DAILY_NEED_DEFAULTS["nutrition"]) + elapsed_hours * rates["nutrition"]
        activity_load = _body_value(state, "activity_load", DAILY_NEED_DEFAULTS["activity_load"]) + elapsed_hours * rates["activity_load"]
        wind_speed = float(environment.get("wind_speed_10m") or 0)
        humidity = float(environment.get("relative_humidity_2m") or 50)

        exposure_rate = 0.0
        if moving:
            rain_factor = rainfall / 20.0
            wind_factor = (wind_speed / 15.0) if wind_speed > 15 else 0.0
            humidity_heat = (humidity / 50.0) if temperature >= 28 else 1.0
            temp_factor = max(0.0, abs(temperature - 22.0) - 8.0) / 4.0 * humidity_heat
            exposure_rate = (rain_factor + wind_factor + temp_factor) * 2.5
        elif not _is_indoor_space(
            state.get("location"),
            state.get("current_node_type"),
            state.get("current_node_properties"),
        ):
            exposure_rate = (rainfall / 40.0 + (wind_speed / 30.0 if wind_speed > 20 else 0)) * 1.2
        else:
            exposure_rate = -3.5

        weather_exposure = float(state["weather_exposure"]) + elapsed_hours * exposure_rate
        illness_load = _body_value(state, "illness_load", DAILY_NEED_DEFAULTS["illness_load"])
        illness_rate = rates["illness_load"]
        if weather_exposure > 80:
            illness_rate += 0.35
        if hydration > 85 or nutrition < 25:
            illness_rate += 0.18
        illness_load += elapsed_hours * illness_rate
        health = float(state["health"])
        health_delta = 0.0
        if hunger > 95:
            health_delta -= 0.08 if sleeping else 0.35
        if fatigue > 97:
            health_delta -= 0.35
        if weather_exposure > 80:
            health_delta -= 0.25
        if illness_load > 55:
            health_delta -= 0.2
        if hydration > 85 or nutrition < 25:
            health_delta -= 0.12
        # Health is a slow-moving reserve, not an hourly death spiral. A
        # properly sleeping resident recovers it, while a stable daytime
        # state at least stops further deterioration.
        if sleeping:
            health_delta += 1.2 if hunger < 85 else 0.4
        elif hunger < 70 and fatigue < 65 and sleep_debt < 50:
            health_delta += 0.08
        health += elapsed_hours * health_delta
        updated = {
            "hunger": _clamp(hunger),
            "fatigue": _clamp(fatigue),
            "sleep_debt": _clamp(sleep_debt),
            "stress": _clamp(stress),
            "attention": _clamp(attention),
            "social_energy": _clamp(social_energy),
            "health": _clamp(health),
            "weather_exposure": _clamp(weather_exposure),
            "hydration": _clamp(hydration),
            "nutrition": _clamp(nutrition),
            "activity_load": _clamp(activity_load),
            "illness_load": _clamp(illness_load),
            "sleep_state": sleep_state,
        }
        sleep_state_sql = ", sleep_state = ?" if "sleep_state" in body_columns else ""
        fields = ["hunger", "fatigue", "sleep_debt", "stress", "attention", "social_energy", "health", "weather_exposure"]
        fields.extend(field for field in DAILY_NEED_DEFAULTS if field in body_columns)
        values = [updated[field] for field in fields]
        values.extend([world_time.isoformat(), tick_number])
        if sleep_state_sql:
            values.append(sleep_state)
        values.append(state["resident_id"])
        assignments = ", ".join(f"{field} = ?" for field in fields)
        conn.execute(
            f"""UPDATE agent_body_states
                SET {assignments}, last_updated_at = ?, last_updated_tick = ?,
                    version = version + 1, updated_at = CURRENT_TIMESTAMP
                    {sleep_state_sql}
                WHERE resident_id = ?""",
            values,
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
                "sleep_state": sleep_state,
                "moving": moving,
                "waiting": waiting,
            }
        )
    return results
