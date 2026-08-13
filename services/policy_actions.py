"""Policy and daily-reflection application services."""


def list_policies(conn, *, rows_to_dicts):
    rows = conn.execute(
        """
        SELECT policies.*, residents.name AS proposer_name
        FROM policies
        LEFT JOIN residents ON residents.id = policies.proposer_id
        ORDER BY policies.id DESC
        """
    ).fetchall()
    return rows_to_dicts(rows)


def submit(conn, payload, *, get_resident, module_state, action_cost, ensure_affordable,
           current_day, add_event, add_memory, update_profile):
    proposer = get_resident(conn, payload.proposer_id)
    if not proposer:
        raise LookupError("提案人不存在")
    schedule_context = module_state(conn, payload.proposer_id)["modules"]["Schedule"]["current_schedule"]
    cost = action_cost(conn, payload.proposer_id, "submit_policy")
    ensure_affordable(conn, payload.proposer_id, cost, "submit_policy")
    day = current_day(conn)
    conn.execute(
        "INSERT INTO policies (title, description, proposer_id) VALUES (?, ?, ?)",
        (payload.title, payload.description, payload.proposer_id),
    )
    description = f"{proposer['name']} 提交校园政策《{payload.title}》：{payload.description}"
    add_event(conn, day, "policy_submit", description)
    add_memory(conn, payload.proposer_id, day, description, importance=3)
    action_cost_result = update_profile(
        conn, payload.proposer_id, "submit_policy", "手动提交政策", cost=cost,
        schedule_context=schedule_context, tool_input={"title": payload.title},
    )
    return {"message": "政策提交成功", "description": description, "action_cost": action_cost_result}


def vote(conn, payload, *, get_resident, action_cost, ensure_affordable, current_day,
         add_event, add_memory, update_profile):
    if payload.vote not in {"yes", "no"}:
        raise ValueError("vote 只能是 yes 或 no")
    resident = get_resident(conn, payload.resident_id)
    policy = conn.execute("SELECT * FROM policies WHERE id = ?", (payload.policy_id,)).fetchone()
    if not resident or not policy:
        raise LookupError("投票人或政策不存在")
    cost = action_cost(conn, payload.resident_id, "observe")
    ensure_affordable(conn, payload.resident_id, cost, "observe")
    column = "yes_votes" if payload.vote == "yes" else "no_votes"
    conn.execute(f"UPDATE policies SET {column} = {column} + 1 WHERE id = ?", (payload.policy_id,))
    day = current_day(conn)
    description = f"{resident['name']} 对政策《{policy['title']}》投票：{payload.vote}"
    add_event(conn, day, "policy_vote", description)
    add_memory(conn, payload.resident_id, day, description, importance=1)
    action_cost_result = update_profile(conn, payload.resident_id, "observe", "参与政策投票", cost=cost)
    return {"message": "投票成功", "description": description, "action_cost": action_cost_result}


def close(conn, policy_id, *, current_day, add_event):
    policy = conn.execute("SELECT * FROM policies WHERE id = ?", (policy_id,)).fetchone()
    if not policy:
        raise LookupError("政策不存在")
    status = "passed" if int(policy["yes_votes"]) >= int(policy["no_votes"]) else "rejected"
    conn.execute("UPDATE policies SET status = ? WHERE id = ?", (status, policy_id))
    day = current_day(conn)
    description = f"政策《{policy['title']}》投票结束，赞成 {policy['yes_votes']}，反对 {policy['no_votes']}，结果：{status}。"
    add_event(conn, day, "policy_close", description)
    return {"message": "政策已结算", "status": status, "description": description}


def reflect(conn, *, current_day, ask_llm, add_memory, add_event):
    day = current_day(conn)
    agents = conn.execute("SELECT * FROM residents ORDER BY id").fetchall()
    events = conn.execute(
        "SELECT description FROM city_events WHERE day = ? ORDER BY id DESC LIMIT 20", (day,)
    ).fetchall()
    event_text = "；".join(row["description"] for row in events) or "今天校园较为平静。"
    results = []
    for agent in agents:
        prompt = f"请以{agent['name']}的第一人称，用一句话总结今天的校园生活。今日事件：{event_text}"
        try:
            reflection = ask_llm(prompt)
        except Exception:
            reflection = f"{agent['name']} 记录了第 {day} 天的校园生活。"
        add_memory(conn, agent["id"], day, reflection, importance=2)
        results.append({"agent_id": agent["id"], "name": agent["name"], "reflection": reflection})
    add_event(conn, day, "daily_reflect", f"第 {day} 天校园日报总结完成，共生成 {len(results)} 条记忆。")
    return {"day": day, "results": results}
