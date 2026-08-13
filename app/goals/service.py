"""Goal lifecycle policies and persistence helpers."""

from app.goals import repository


def infer_goal_category(goal_text):
    text = str(goal_text or "")
    if any(word in text for word in ["成绩", "课程", "考研", "论文", "学习", "实验", "奖学金"]):
        return "study"
    if any(word in text for word in ["销售", "创业", "消费", "订单", "收入", "商机"]):
        return "business"
    if any(word in text for word in ["活动", "社团", "朋友", "交流", "合作"]):
        return "social"
    if any(word in text for word in ["秩序", "设施", "服务", "管理", "安全"]):
        return "service"
    return "general"


def seed_long_term_goals(conn, *, current_day):
    day = current_day(conn)
    for resident in repository.residents_without_long_term_goal(conn):
        repository.insert_long_term_goal(conn, resident["id"], resident["goal"], infer_goal_category(resident["goal"]), day + 14, day)


def seed_multiscale_goals(conn):
    for row in repository.legacy_long_term_goals(conn):
        repository.insert_legacy_multiscale_goal(conn, row)


def record_goal_revision(conn, goal_id, resident_id, revision_type, *, current_day, json_dumps,
                         before=None, after=None, reason="", trigger_type="runtime", tick_id=None):
    repository.insert_goal_revision(conn, (goal_id, resident_id, current_day(conn), tick_id, revision_type, json_dumps(before or {}, ensure_ascii=False), json_dumps(after or {}, ensure_ascii=False), reason[:240], trigger_type, json_dumps({"source": "multiscale-goal-runtime-v1"}, ensure_ascii=False)))


def parse_goal_deadline(value, *, parse_world_datetime):
    if not value or str(value).startswith("simulation-day:"):
        return None
    return parse_world_datetime(value)


def multiscale_goal_templates(resident, long_goal, *, role_group):
    category = str(long_goal.get("category") or infer_goal_category(long_goal.get("title")))
    role = role_group(resident.get("role"))
    templates = {
        "study": ("形成可检查的阶段学习成果", "完成当前阶段最重要的一项学习任务"),
        "business": ("验证近期校园需求并改进服务", "完成一次具体服务并记录反馈"),
        "social": ("通过持续互动发展一段有意义的关系", "履行一次交流、帮助或协作约定"),
        "service": ("改善一个可观察的校园运行问题", "完成一次巡查、协调或服务响应"),
        "general": ("把长期方向转化为一个可验证的阶段项目", "完成当前阶段最可行的一步"),
    }
    medium, short = templates.get(category, templates["general"])
    if role == "teacher" and category == "general":
        medium, short = "推进教学、指导或研究中的一个阶段成果", "完成一次具体教学或指导任务"
    elif role == "business" and category == "general":
        medium, short, category = *templates["business"], "business"
    elif role == "service" and category == "general":
        medium, short, category = *templates["service"], "service"
    return category, f"{medium}：围绕《{long_goal['title']}》", short


def ensure_goal_trajectory_episode(conn, goal, world_time):
    lookup_params = (goal["resident_id"], goal["id"], goal["horizon"])
    row = repository.trajectory_episode(conn, *lookup_params)
    if row:
        return dict(row)
    cursor = repository.create_trajectory_episode(conn, (goal["resident_id"], goal["id"], goal["horizon"], goal["title"], world_time.isoformat(), f"围绕{goal['horizon']}层目标推进：{goal['title']}"))
    row = repository.trajectory_episode(conn, *lookup_params)
    return dict(row) if row else {"id": cursor.lastrowid}


def attach_goal_context_to_plan(plan, goal_context):
    plan = dict(plan or {})
    chain = {
        "long_goal_id": goal_context["long"]["id"], "long_goal": goal_context["long"]["title"],
        "medium_goal_id": goal_context["medium"]["id"], "medium_goal": goal_context["medium"]["title"],
        "short_goal_id": goal_context["short"]["id"], "short_goal": goal_context["short"]["title"],
        "commitment_id": goal_context["commitment"]["id"] if goal_context.get("commitment") else None,
        "commitment": goal_context["commitment"]["title"] if goal_context.get("commitment") else "",
    }
    plan["goal_chain"] = chain
    plan["steps"] = [{**dict(step), "long_goal_id": chain["long_goal_id"], "medium_goal_id": chain["medium_goal_id"], "short_goal_id": chain["short_goal_id"], "commitment_id": chain["commitment_id"]} for step in plan.get("steps") or []]
    return plan
