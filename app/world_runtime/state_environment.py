"""Agent state, location context, and environment runtime operations."""

from dataclasses import dataclass
from typing import Any, Mapping
import logging
import random

from tools.city_tools import VALID_LOCATIONS
from app.spatial.location_catalog import (
    best_real_location,
    is_real_world_location,
    rank_real_location_options,
    real_location_options,
    supports_action as real_location_supports_action,
)

_MODULE_NAME = __name__
logger = logging.getLogger(__name__)
_OVERRIDABLE_DEFAULTS = {
    "is_location_open_at_hour",
    "realistic_location_for_context",
    "role_group",
}


@dataclass(frozen=True)
class StateEnvironmentDependencies:
    """Explicit composition-root bindings for state and environment runtime."""

    values: Mapping[str, Any]

    def apply(self):
        configure(**dict(self.values))

def configure(**bindings):
    module_globals = globals()
    for name, value in bindings.items():
        if name.startswith("__"):
            continue
        current = module_globals.get(name)
        if (
            callable(current)
            and getattr(current, "__module__", None) == _MODULE_NAME
            and name not in _OVERRIDABLE_DEFAULTS
        ):
            continue
        module_globals[name] = value
    module_globals["__name__"] = _MODULE_NAME


def role_group(role):
    role = str(role or "")
    if any(token in role for token in ("教师", "教授", "辅导员", "教职")):
        return "teacher"
    if any(token in role for token in ("商", "店", "餐")):
        return "business"
    if any(token in role for token in ("后勤", "管理", "保安")):
        return "service"
    return "student"


def is_location_open_at_hour(location, hour):
    hours = {
        "宿舍区": (0, 24), "教学楼": (7, 22), "图书馆": (8, 22),
        "食堂": (6, 21), "操场": (6, 22), "商业街": (9, 22), "校务处": (8, 18),
    }
    start, end = hours.get(location, (0, 24))
    return start <= int(hour) < end if end != 24 else int(hour) >= start


def realistic_location_for_context(role, hour, weather="", current_location="", **_unused):
    """Minimal standalone fallback; production supplies the richer planner."""
    group = role_group(role)
    if group == "teacher":
        preferred = "教学楼"
    elif group == "business":
        preferred = "商业街"
    elif group == "service":
        preferred = "校务处"
    else:
        preferred = "图书馆" if 8 <= int(hour) < 22 else "宿舍区"
    return preferred if is_location_open_at_hour(preferred, hour) else "宿舍区"


def is_residential_location(location):
    """Accept real-world OSM residence names as well as the legacy dorm zone."""
    text = str(location or "").lower()
    return text == "宿舍区" or any(
        token in text for token in ("宿舍", "公寓", "住宅", "residence")
    )


_PHYSICAL_SPACE_PATTERNS = {
    "canteen_crowd": ("食堂", "餐厅", "餐饮", "清晏楼", "清芬", "观畴", "紫荆园", "桃李园"),
    "dorm_crowd": ("宿舍", "公寓", "寝室", "住宅", "双清"),
    "library_crowd": ("图书馆", "阅览"),
    "classroom_crowd": ("教学", "教室", "学堂", "实验", "科研"),
    "playground_crowd": ("操场", "体育", "球场", "运动"),
    "commercial_crowd": ("商业", "商店", "超市", "咖啡", "服务"),
}


def derive_environment_from_spatial_facts(conn, values):
    """Replace crowd templates with observed location, queue and service facts.

    This intentionally does not invent a rush-hour percentage. Every category
    starts at zero; a category with no mapped real spatial entity or no Agent
    occupancy remains zero rather than inheriting a time-slot template.
    """
    rows = conn.execute(
        """
        SELECT n.name, n.node_type, n.capacity, n.status,
               COUNT(s.resident_id) AS occupancy
        FROM spatial_nodes n
        LEFT JOIN agent_spatial_states s ON s.current_node_id = n.id
        GROUP BY n.id, n.name, n.node_type, n.capacity, n.status
        """
    ).fetchall()
    movement = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN movement_status NOT IN ('idle', 'arrived') THEN 1 ELSE 0 END) AS moving
           FROM agent_spatial_states"""
    ).fetchone()
    queue = conn.execute(
        "SELECT COUNT(*) AS waiting FROM spatial_admission_queue WHERE status IN ('waiting', 'queued')"
    ).fetchone()
    resources = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status NOT IN ('open', 'available', '开放') OR available_units <= 0 THEN 1 ELSE 0 END) AS unavailable
           FROM spatial_resources"""
    ).fetchone()

    aggregates = {key: {"occupancy": 0, "capacity": 0, "seen": 0, "peak_ratio": 0.0} for key in _PHYSICAL_SPACE_PATTERNS}
    for row in rows:
        name = str(row["name"] or "")
        node_type = str(row["node_type"] or "")
        occupancy = int(row["occupancy"] or 0)
        capacity = max(0, int(row["capacity"] or 0))
        for field, patterns in _PHYSICAL_SPACE_PATTERNS.items():
            if any(token in name for token in patterns):
                data = aggregates[field]
                data["occupancy"] += occupancy
                data["capacity"] += capacity
                data["seen"] += 1
                data["peak_ratio"] = max(data["peak_ratio"], occupancy / max(1, capacity or 20))
                break
        else:
            if node_type in {"classroom", "library", "canteen", "dorm"}:
                field = {"classroom": "classroom_crowd", "library": "library_crowd", "canteen": "canteen_crowd", "dorm": "dorm_crowd"}[node_type]
                data = aggregates[field]
                data["occupancy"] += occupancy
                data["capacity"] += capacity
                data["seen"] += 1
                data["peak_ratio"] = max(data["peak_ratio"], occupancy / max(1, capacity or 20))

    factual = dict(values)
    for field in _PHYSICAL_SPACE_PATTERNS:
        factual[field] = 0
    percentages = []
    for field, data in aggregates.items():
        if not data["seen"]:
            continue
        # Unknown OSM capacity must not make a present Agent disappear.  A
        # conservative per-place fallback makes the uncertainty visible while
        # retaining the observation-driven numerator.
        effective_capacity = data["capacity"] or max(1, data["seen"] * 20)
        ratio = max(data["peak_ratio"], data["occupancy"] / effective_capacity)
        ratio = min(1.0, ratio)
        factual[field] = round(ratio * 100)
        percentages.append(factual[field])

    total = int(movement["total"] or 0) if movement else 0
    moving = int(movement["moving"] or 0) if movement and movement["moving"] is not None else 0
    waiting = int(queue["waiting"] or 0) if queue else 0
    resource_total = int(resources["total"] or 0) if resources else 0
    unavailable = int(resources["unavailable"] or 0) if resources and resources["unavailable"] is not None else 0
    factual["campus_flow"] = round(min(100, (moving + waiting) * 100 / max(1, total)))
    factual["resource_pressure"] = round(min(100, (unavailable * 100 / max(1, resource_total)) + waiting * 8))
    factual["traffic_status"] = "拥堵" if factual["campus_flow"] >= 70 else "正常"
    factual["network_status"] = "拥堵" if factual.get("dorm_crowd", 0) >= 75 else "稳定"
    factual["spatial_facts_source"] = "agent_presence_queue_resource"
    factual["spatial_observed_agent_count"] = total
    return factual


def get_agent_module_state(conn, resident_id):
    ensure_agent_profile_table(conn)
    ensure_memory_columns(conn)
    resident = conn.execute("SELECT * FROM residents WHERE id = ?", (resident_id,)).fetchone()
    if not resident:
        return None

    profile = conn.execute(
        "SELECT * FROM agent_profiles WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    inventory_rows = conn.execute(
        "SELECT item_name, quantity FROM inventory WHERE resident_id = ? ORDER BY item_name",
        (resident_id,),
    ).fetchall()
    relationship_rows = conn.execute(
        """
        SELECT relationships.to_resident_id, residents.name, residents.role,
               relationships.score, relationships.notes
        FROM relationships
        JOIN residents ON residents.id = relationships.to_resident_id
        WHERE relationships.from_resident_id = ?
        ORDER BY relationships.score DESC
        LIMIT 10
        """,
        (resident_id,),
    ).fetchall()
    current_day = get_current_day(conn)
    memory_rows = conn.execute(
        """
        SELECT day, content, importance, memory_type, tags, source, access_count, last_accessed_at, created_at
        FROM memories
        WHERE resident_id = ? AND day <= ?
        ORDER BY id DESC
        LIMIT 8
        """,
        (resident_id, current_day),
    ).fetchall()

    profile_data = dict(profile) if profile else {}
    schedule = load_json_text(profile_data.get("schedule"), [])
    perception = load_json_text(profile_data.get("perception"), {})
    skills = load_json_text(profile_data.get("skills"), {})
    strategy = load_json_text(profile_data.get("strategy"), {})
    hierarchy_level = profile_data.get("hierarchy_level", 1)
    hierarchy_title = get_hierarchy_title(hierarchy_level)
    env = get_campus_environment(conn)
    schedule_context = get_schedule_context(schedule, env)

    return {
        "id": resident["id"],
        "name": resident["name"],
        "gender": profile_data.get("gender", "未设置"),
        "avatar_style": profile_data.get("avatar_style", "简单卡通校园人物"),
        "avatar_image": profile_data.get("avatar_image", ""),
        "organization": profile_data.get("organization", "学生"),
        "hierarchy_level": hierarchy_level,
        "hierarchy_title": hierarchy_title,
        "modules": {
            "Physical": {
                "description": "我是谁、我在哪",
                "position": resident["location"],
                "role": resident["role"],
                "energy": profile_data.get("energy", 80),
                "time_budget": profile_data.get("time_budget", 100),
                "money": resident["money"],
                "mood": profile_data.get("mood", "平稳"),
                "inventory": rows_to_dicts(inventory_rows),
            },
            "Mental": {
                "description": "我想干什么",
                "goal": resident["goal"],
                "personality": resident["personality"],
                "personality_traits": strategy.get("personality_traits", {}),
                "personality_version": strategy.get("personality_version", ""),
                "task": profile_data.get("current_task", "适应校园生活"),
            },
            "Social": {
                "description": "我认识谁",
                "relationships": rows_to_dicts(relationship_rows),
            },
            "Memory": {
                "description": "我经历过什么",
                "memories": rows_to_dicts(memory_rows),
            },
            "Schedule": {
                "description": "我现在该干什么",
                "schedule": schedule,
                "current_schedule": schedule_context,
            },
            "Perception": {
                "description": "我现在看见什么",
                "perception": perception,
            },
        },
    }


def location_options_for_context(role, hour, weather="", current_location="", conn=None, env=None, agent=None):
    group = role_group(role)
    weather_text = str(weather or "")
    rainy = any(token in weather_text for token in ("雨", "雷", "雪", "雾", "大风"))
    if conn:
        concrete_options = real_location_options(
            conn,
            role,
            hour,
            current_location=current_location,
            weather=weather_text,
        )
        if concrete_options:
            return concrete_options
    if 0 <= hour < 6:
        base = {
            "student": [("宿舍区", 82), ("图书馆", 8), ("教学楼", 5), ("操场", 2), (current_location, 3)],
            "teacher": [("宿舍区", 62), ("教学楼", 12), ("图书馆", 14), ("校务处", 5), (current_location, 7)],
            "business": [("商业街", 26), ("宿舍区", 42), ("食堂", 8), ("校务处", 6), (current_location, 18)],
            "service": [("宿舍区", 34), ("校务处", 26), ("食堂", 14), ("图书馆", 8), (current_location, 18)],
        }.get(group, [("宿舍区", 80), (current_location, 20)])
    elif 6 <= hour < 9:
        base = {
            "student": [("食堂", 34), ("教学楼", 24), ("宿舍区", 18), ("操场", 10), ("图书馆", 8), (current_location, 6)],
            "teacher": [("教学楼", 34), ("食堂", 18), ("校务处", 16), ("图书馆", 16), (current_location, 16)],
            "business": [("商业街", 30), ("食堂", 30), ("校务处", 8), (current_location, 32)],
            "service": [("食堂", 28), ("校务处", 28), ("宿舍区", 18), ("教学楼", 12), (current_location, 14)],
        }.get(group, [("食堂", 30), ("教学楼", 25), (current_location, 20)])
    elif 9 <= hour < 11:
        base = {
            "student": [("教学楼", 48), ("图书馆", 24), ("操场", 8), ("商业街", 6), (current_location, 14)],
            "teacher": [("教学楼", 50), ("图书馆", 18), ("校务处", 18), (current_location, 14)],
            "business": [("商业街", 55), ("食堂", 20), ("校务处", 8), (current_location, 17)],
            "service": [("校务处", 38), ("教学楼", 20), ("食堂", 18), ("图书馆", 10), (current_location, 14)],
        }.get(group, [("教学楼", 35), ("图书馆", 25), (current_location, 15)])
    elif 11 <= hour < 14:
        base = {
            "student": [("食堂", 42), ("教学楼", 18), ("图书馆", 16), ("商业街", 10), (current_location, 14)],
            "teacher": [("食堂", 32), ("教学楼", 28), ("校务处", 12), ("图书馆", 12), (current_location, 16)],
            "business": [("食堂", 36), ("商业街", 42), ("校务处", 6), (current_location, 16)],
            "service": [("食堂", 30), ("校务处", 30), ("教学楼", 14), (current_location, 26)],
        }.get(group, [("食堂", 38), ("教学楼", 20), (current_location, 15)])
    elif 14 <= hour < 17:
        base = {
            "student": [("教学楼", 38), ("图书馆", 26), ("商业街", 10), ("操场", 8), (current_location, 18)],
            "teacher": [("教学楼", 38), ("图书馆", 24), ("校务处", 20), (current_location, 18)],
            "business": [("商业街", 56), ("食堂", 16), ("校务处", 8), (current_location, 20)],
            "service": [("校务处", 34), ("图书馆", 18), ("教学楼", 18), ("食堂", 12), (current_location, 18)],
        }.get(group, [("教学楼", 32), ("图书馆", 24), (current_location, 18)])
    elif 17 <= hour < 21:
        base = {
            "student": [("食堂", 30), ("操场", 22), ("图书馆", 18), ("商业街", 14), ("宿舍区", 10), (current_location, 6)],
            "teacher": [("食堂", 24), ("图书馆", 22), ("教学楼", 18), ("操场", 10), ("宿舍区", 12), (current_location, 14)],
            "business": [("商业街", 46), ("食堂", 26), ("宿舍区", 8), (current_location, 20)],
            "service": [("宿舍区", 24), ("食堂", 24), ("校务处", 18), ("操场", 12), (current_location, 22)],
        }.get(group, [("食堂", 28), ("操场", 18), ("宿舍区", 16), (current_location, 12)])
    else:
        base = {
            "student": [("宿舍区", 56), ("图书馆", 18), ("操场", 8), ("教学楼", 8), (current_location, 10)],
            "teacher": [("宿舍区", 42), ("图书馆", 20), ("教学楼", 14), (current_location, 24)],
            "business": [("商业街", 28), ("宿舍区", 30), ("食堂", 8), (current_location, 34)],
            "service": [("宿舍区", 34), ("校务处", 20), ("食堂", 12), (current_location, 34)],
        }.get(group, [("宿舍区", 50), (current_location, 20)])
    if rainy:
        base = [(location, weight * 0.25 if location == "操场" else weight) for location, weight in base]
    for rule in active_schedule_rules(conn, role, hour, env):
        if rule.get("location") in VALID_LOCATIONS:
            noise = float(rule.get("noise") or 0)
            weight = float(rule.get("base_weight") or 1.0) * 8
            weight *= 1 + random.uniform(-noise, noise)
            base.append((rule["location"], weight))
    if agent:
        bias = action_noise_for_agent(agent)
        adjusted = []
        for location, weight in base:
            if location in {"教学楼", "图书馆"}:
                weight *= bias["study"]
            if location in {"食堂", "操场", "商业街"}:
                weight *= bias["social"]
            if location in {"校务处", "宿舍区"}:
                weight *= bias["routine"]
            adjusted.append((location, weight))
        base = adjusted
    if conn and env:
        base = [
            (location, weight * causal_multiplier_for_target(conn, env, "location", location))
            for location, weight in base
        ]
    if conn and agent and agent.get("id"):
        memory_factors = spatial_memory_location_factors(
            conn,
            agent["id"],
            branch_key=active_world_branch_key(conn),
        )
        base = [
            (location, weight * memory_factors.get(location, 1.0))
            for location, weight in base
        ]
    open_options = [(location, weight) for location, weight in base if is_location_open_at_hour(location, hour)]
    return open_options or [("宿舍区", 1)]


def apply_realism_constraints_to_decision(conn, agent, decision, perception, world_time):
    decision = dict(decision or {})
    hour = world_time.hour
    env = perception.get("environment", {}) if isinstance(perception, dict) else {}
    weather = env.get("weather", "")
    action = str(decision.get("action") or "observe")
    destination = str(decision.get("location") or agent["location"])
    role = str(agent.get("role") or "")
    notes = []
    at_residence = is_residential_location(agent.get("location"))

    action_location_defaults = {
        "attend_class": "教学楼",
        "queue": "食堂",
        "consume": "食堂" if 6 <= hour < 14 or 17 <= hour < 21 else "商业街",
        "rest": "宿舍区",
        "club_activity": "操场",
        "request_leave": "校务处",
    }
    if action in action_location_defaults:
        ranked = rank_real_location_options(
            conn, agent.get("id"), action, hour=hour, weather=weather
        ) if agent.get("id") else []
        decision["location_candidates"] = ranked[:5]
        concrete = next((item["location"] for item in ranked if item["available"]), None)
        concrete = concrete or best_real_location(conn, action, current_location=agent.get("location", ""))
        if concrete:
            destination = concrete
        else:
            preferred = action_location_defaults[action]
            if is_location_open_at_hour(preferred, hour):
                destination = preferred

    destination_is_real = is_real_world_location(conn, destination)
    if destination not in VALID_LOCATIONS and not is_residential_location(destination) and not destination_is_real:
        notes.append("目的地不存在，改为当前位置观察")
        destination = (
            agent["location"]
            if agent["location"] in VALID_LOCATIONS or is_real_world_location(conn, agent["location"])
            else "宿舍区"
        )
        action = "observe"

    if destination in VALID_LOCATIONS and not is_location_open_at_hour(destination, hour):
        adjusted = realistic_location_for_context(role, hour, weather, current_location=agent["location"])
        notes.append(f"{destination}当前不适合进入，调整到{adjusted}")
        destination = adjusted
        action = "reflect" if destination == "宿舍区" and (hour < 6 or hour >= 22) else "observe"

    if 0 <= hour < 6 and at_residence:
        # Sleep is determined by the physical place, not the resident's role.
        # A shop owner or staff member already at home must not be sent on a
        # fake route to the legacy ``宿舍区`` label.
        destination = agent["location"]
        action = "rest"
        decision["goal"] = "夜间休息，恢复精力"
        notes.append("深夜处于住宿节点，优先休息恢复")
    elif 0 <= hour < 6 and role_group(role) == "student":
        night_rest = best_real_location(conn, "rest", current_location=agent.get("location", ""))
        if destination != (night_rest or "宿舍区") and random.random() < 0.88:
            notes.append("深夜学生活动概率较低，回到宿舍区休息")
            destination = night_rest or "宿舍区"
            action = "rest"

    if (destination in {"操场", "商业街"} or "露天" in destination) and any(token in str(weather or "") for token in ("雨", "雷", "雪", "大风", "暴雨", "大雨", "中雨", "严酷", "低落")):
        # Severe weather should be an exceptional reason to remain outdoors,
        # not a noisy coin flip that regularly defeats the safety constraint.
        if random.random() < 0.96:
            adjusted = realistic_location_for_context(role, hour, weather, current_location=agent["location"])
            notes.append(f"恶劣天气({weather})降低户外活动意愿，改到室内地点{adjusted}")
            destination = adjusted
            if action == "club_activity":
                action = "chat"
            elif action == "move":
                action = "observe"

    if action == "move" and destination == agent["location"]:
        action = "observe"
        notes.append("已在目标地点，改为现场观察")

    if action == "attend_class" and destination != "教学楼" and not real_location_supports_action(destination, action):
        action = "observe"
        notes.append("课程活动无法在当前空间完成，改为观察学习状态")
    if action in {"queue", "consume"} and destination not in {"食堂", "商业街"} and not real_location_supports_action(destination, action):
        action = "observe"
        notes.append("消费/排队行为与当前空间不匹配，改为观察")
    if action == "club_activity" and destination != "操场" and not real_location_supports_action(destination, action):
        action = "chat"
        notes.append("社团活动转为室内轻量交流")
    if action == "request_leave" and not (
        real_location_supports_action(destination, action)
        or is_location_open_at_hour("校务处", hour)
    ):
        action = "reflect"
        destination = "宿舍区" if is_location_open_at_hour("宿舍区", hour) else agent["location"]
        notes.append("校务处未开放，请假改为整理申请理由")

    if random.random() < 0.04:
        alternate = realistic_location_for_context(role, hour, weather, current_location=agent["location"])
        if alternate != destination:
            notes.append(f"受到临时状态扰动，短暂偏离计划到{alternate}")
            destination = alternate
            action = "move" if alternate != agent["location"] else "observe"
            decision["plan_relation"] = "adjust"

    decision["action"] = action
    decision["location"] = destination
    if notes:
        decision["constraint_notes"] = notes
        reason = str(decision.get("reason") or "")
        decision["reason"] = f"{reason}（现实约束：{'；'.join(notes)}）"[:220]
    return decision


def auto_update_environment(conn, day):
    previous = get_campus_environment(conn, day)
    weather = random.choice(["晴", "多云", "小雨", "闷热", "大风"])
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][(day - 1) % 7]
    time_slot = random.choice(["上午", "中午", "下午", "晚上"])
    temperature = random.randint(18, 32)
    rainfall = random.randint(20, 80) if weather == "小雨" else random.randint(0, 15)

    semester_stage = previous.get("semester_stage", "平时周")
    exam_pressure = int(previous.get("exam_pressure", 35))
    assignment_pressure = int(previous.get("assignment_pressure", 40))
    activity_heat = int(previous.get("activity_heat", 50))

    if day % 7 == 0:
        semester_stage = "考试周"
        exam_pressure = min(100, exam_pressure + 25)
        assignment_pressure = min(100, assignment_pressure + 15)
        activity_heat = max(20, activity_heat - 15)
        event_name = "期末复习"
    elif day % 5 == 0:
        semester_stage = "活动周"
        exam_pressure = max(10, exam_pressure - 10)
        assignment_pressure = max(10, assignment_pressure - 5)
        activity_heat = min(100, activity_heat + 25)
        event_name = "校园社团节"
    else:
        event_name = random.choice(["社团招新", "普通教学日", "讲座通知", "运动训练"])
        exam_pressure = max(10, min(100, exam_pressure + random.randint(-8, 8)))
        assignment_pressure = max(10, min(100, assignment_pressure + random.randint(-8, 8)))
        activity_heat = max(10, min(100, activity_heat + random.randint(-10, 10)))

    study_atmosphere = max(10, min(100, 35 + exam_pressure // 2 + assignment_pressure // 3))
    event_intensity = max(10, min(100, activity_heat + random.randint(-10, 15)))
    campus_flow = max(10, min(100, 45 + activity_heat // 2 + random.randint(-10, 10)))
    classroom_crowd = max(10, min(100, 40 + assignment_pressure // 2 + random.randint(-10, 10)))
    canteen_crowd = max(10, min(100, campus_flow + (20 if time_slot in {"中午", "晚上"} else 0) + random.randint(-10, 10)))
    library_crowd = max(10, min(100, 35 + exam_pressure // 2 + random.randint(-10, 15)))
    dorm_crowd = max(10, min(100, 35 + (20 if time_slot == "晚上" else 0) + random.randint(-10, 15)))
    playground_crowd = max(10, min(100, 30 + activity_heat // 2 - rainfall // 3 + random.randint(-10, 10)))
    commercial_crowd = max(10, min(100, 35 + activity_heat // 2 + random.randint(-5, 20)))

    traffic_status = "拥堵" if campus_flow > 75 else "正常"
    network_status = "拥堵" if dorm_crowd > 70 and time_slot == "晚上" else "稳定"
    safety_level = max(50, min(100, 95 - campus_flow // 8 - event_intensity // 10))
    resource_pressure = max(10, min(100, (canteen_crowd + library_crowd + classroom_crowd) // 3))
    campus_mood = "紧张" if exam_pressure > 75 else ("活跃" if activity_heat > 70 else "平稳")
    consumption_index = round(max(0.5, min(1.8, 0.7 + activity_heat / 110 + commercial_crowd / 220 + random.uniform(-0.1, 0.1))), 2)

    values = {
        "weather": weather,
        "semester_stage": semester_stage,
        "time_slot": time_slot,
        "weekday": weekday,
        "temperature": temperature,
        "rainfall": rainfall,
        "exam_pressure": exam_pressure,
        "assignment_pressure": assignment_pressure,
        "study_atmosphere": study_atmosphere,
        "activity_heat": activity_heat,
        "event_name": event_name,
        "event_intensity": event_intensity,
        "campus_flow": campus_flow,
        "classroom_crowd": classroom_crowd,
        "canteen_crowd": canteen_crowd,
        "library_crowd": library_crowd,
        "dorm_crowd": dorm_crowd,
        "playground_crowd": playground_crowd,
        "commercial_crowd": commercial_crowd,
        "traffic_status": traffic_status,
        "network_status": network_status,
        "safety_level": safety_level,
        "resource_pressure": resource_pressure,
        "campus_mood": campus_mood,
        "consumption_index": consumption_index,
    }

    try:
        real_weather = fetch_real_weather()
        values.update({key: real_weather[key] for key in ["weather", "temperature", "rainfall", "weather_source", "weather_observed_at"]})
    except Exception as exc:
        logger.warning("Falling back to simulated weather: %s", exc)
        values["weather_source"] = "simulation"
        values["weather_observed_at"] = ""
    values = derive_environment_from_weather(values)
    values = derive_environment_from_real_time(values)
    # Materialize local, observable conditions from actual agent occupancy,
    # facility availability and the latest weather.  This deliberately happens
    # after environment persistence so node state never depends on a time-slot
    # crowd template.
    from app.spatial.physical_state_service import refresh_spatial_physical_states
    worlds = conn.execute("SELECT DISTINCT world_key FROM spatial_nodes").fetchall()
    for world in worlds:
        refresh_spatial_physical_states(conn, world_key=world["world_key"], environment=values)
    values = derive_environment_from_spatial_facts(conn, values)
    save_environment_values(conn, day, values)
    conn.commit()
    maybe_generate_environment_event(conn, day)
    return get_campus_environment(conn, day)
