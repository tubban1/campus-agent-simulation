"""Shared Agent state and action-feasibility rules."""


def calculate_action_cost(conn, resident_id, action, tool_input=None, success=True, *, campus_environment, space_snapshot):
    tool_input = tool_input or {}
    costs = {"move": {"energy": 8, "time": 12}, "chat": {"energy": 3, "time": 10}, "buy_sell": {"energy": 5, "time": 15}, "submit_policy": {"energy": 6, "time": 25}, "create_group": {"energy": 7, "time": 28}, "join_group": {"energy": 3, "time": 12}, "leave_group": {"energy": 2, "time": 8}, "observe": {"energy": 2, "time": 8}}
    cost, environment = dict(costs.get(action, costs["observe"])), campus_environment(conn)
    if action == "move":
        space = next((item for item in space_snapshot(conn)["spaces"] if item["location"] == tool_input.get("destination")), None)
        if space and int(space["crowd_percent"]) >= 70: cost["time"] += 6; cost["energy"] += 2
        if int(environment.get("rainfall", 0)) >= 20: cost["time"] += 4; cost["energy"] += 2
        if environment.get("traffic_status") == "拥堵": cost["time"] += 3
    if action == "buy_sell" and int(environment.get("commercial_crowd", 0)) >= 70: cost["time"] += 5
    if action == "observe" and int(environment.get("study_atmosphere", 0)) >= 75: cost["time"] = max(5, cost["time"] - 2)
    if not success: cost["energy"] += 3; cost["time"] += 5
    return cost


def ensure_action_affordable(conn, resident_id, cost, action):
    profile = conn.execute("SELECT energy, time_budget FROM agent_profiles WHERE resident_id = ?", (resident_id,)).fetchone()
    if not profile: return
    if action != "observe" and int(profile["energy"]) < int(cost["energy"]): raise ValueError("精力不足，需要先休息或进行低成本观察")
    if int(profile["time_budget"]) < int(cost["time"]): raise ValueError("今日可用时间不足，需要等待下一模拟日")


def choose_mood(energy, action, success=True):
    if not success: return "受挫"
    if energy <= 25: return "疲惫"
    return {"chat": "放松", "buy_sell": "满足", "submit_policy": "认真", "move": "行动中"}.get(action, "观察中")


def recover_agents_for_new_day(conn, day, *, ensure_profile_table, add_event):
    ensure_profile_table(conn)
    conn.execute("""UPDATE agent_profiles SET energy = CASE WHEN energy + 16 > 100 THEN 100 ELSE energy + 16 END,
                 time_budget = 100, current_task = '开始新的一天，准备执行日程'""")
    add_event(conn, day, "daily_recovery", "新的一天开始：所有 Agent 恢复部分精力，并重置每日时间预算。")
