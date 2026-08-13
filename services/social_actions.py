"""Manual social, goal, and tool action transactions."""


def create_goal(conn, payload, *, get_resident, ensure_tables, current_day, seed_goals, add_event):
    if not get_resident(conn, payload.resident_id): raise LookupError("Agent 不存在")
    ensure_tables(conn); day = current_day(conn)
    cursor = conn.execute("INSERT INTO long_term_goals (resident_id, title, category, deadline_day, last_update_day) VALUES (?, ?, ?, ?, ?)", (payload.resident_id, payload.title, payload.category, payload.deadline_day or day + 14, day)); seed_goals(conn)
    unified = conn.execute("SELECT id FROM agent_goals WHERE legacy_long_term_goal_id = ?", (cursor.lastrowid,)).fetchone(); add_event(conn, day, "long_term_goal", f"Agent {payload.resident_id} 新增长期目标《{payload.title}》。")
    return {"message": "长期目标已创建", "goal_id": cursor.lastrowid, "agent_goal_id": unified["id"] if unified else None}


def create_group(conn, payload, *, ensure_tables, current_day, json_dumps, evolve_relationship, add_event):
    ensure_tables(conn); ids = [payload.leader_id] + [member_id for member_id in payload.member_ids if member_id != payload.leader_id]
    residents = conn.execute(f"SELECT id FROM residents WHERE id IN ({','.join(['?'] * len(ids))})", ids).fetchall()
    if len(residents) != len(set(ids)): raise LookupError("有 Agent 不存在")
    day = current_day(conn); roles = {str(member_id): ("负责人" if member_id == payload.leader_id else "成员") for member_id in ids}
    cursor = conn.execute("INSERT INTO group_goals (name, group_type, leader_id, member_ids, roles, shared_goal, deadline_day, current_plan) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (payload.name, payload.group_type, payload.leader_id, json_dumps(ids, ensure_ascii=False), json_dumps(roles, ensure_ascii=False), payload.shared_goal, payload.deadline_day or day + 10, payload.current_plan))
    for from_id in ids:
        for to_id in ids:
            if from_id != to_id: evolve_relationship(conn, from_id, to_id, "group_goal", f"共同目标：{payload.shared_goal}", 2, 3, 0)
    add_event(conn, day, "group_goal", f"群体「{payload.name}」成立，共同目标：{payload.shared_goal}。")
    return {"message": "群体目标已创建", "group_id": cursor.lastrowid, "member_ids": ids}


def move(conn, payload, *, module_state, action_cost, ensure_affordable, ensure_destination, move_resident, advance_goal, update_profile):
    schedule = module_state(conn, payload.resident_id)["modules"]["Schedule"]["current_schedule"]; cost = action_cost(conn, payload.resident_id, "move", {"destination": payload.destination}); ensure_affordable(conn, payload.resident_id, cost, "move"); ensure_destination(conn, payload.destination)
    result = move_resident(conn, payload.resident_id, payload.destination); result["long_term_goal"] = advance_goal(conn, payload.resident_id, "move", True); result["action_cost"] = update_profile(conn, payload.resident_id, "move", "手动移动", cost=cost, schedule_context=schedule, tool_input={"destination": payload.destination}); return result


def chat(conn, payload, *, module_state, action_cost, ensure_affordable, chat_between, evolve_relationship, advance_goal, update_profile):
    schedule = module_state(conn, payload.speaker_id)["modules"]["Schedule"]["current_schedule"]; cost = action_cost(conn, payload.speaker_id, "chat"); ensure_affordable(conn, payload.speaker_id, cost, "chat"); result = chat_between(conn, payload.speaker_id, payload.listener_id, payload.message)
    result["social_update"] = {"speaker": evolve_relationship(conn, payload.speaker_id, payload.listener_id, "chat", "日常交流", 3, 2, -1), "listener": evolve_relationship(conn, payload.listener_id, payload.speaker_id, "chat", "回应交流", 2, 2, -1)}; result["long_term_goal"] = advance_goal(conn, payload.speaker_id, "chat", True); result["action_cost"] = update_profile(conn, payload.speaker_id, "chat", "手动交流", cost=cost, schedule_context=schedule, tool_input={"target_id": payload.listener_id}); return result


def buy_sell(conn, payload, *, module_state, action_cost, ensure_affordable, transact, advance_goal, update_profile):
    schedule = module_state(conn, payload.buyer_id)["modules"]["Schedule"]["current_schedule"]; cost = action_cost(conn, payload.buyer_id, "buy_sell"); ensure_affordable(conn, payload.buyer_id, cost, "buy_sell"); result = transact(conn, payload.buyer_id, payload.seller_id, payload.item_name, payload.quantity, payload.unit_price)
    result["long_term_goal"] = advance_goal(conn, payload.buyer_id, "buy_sell", True); result["action_cost"] = update_profile(conn, payload.buyer_id, "buy_sell", "手动交易", cost=cost, schedule_context=schedule, tool_input={"seller_id": payload.seller_id}); return result
