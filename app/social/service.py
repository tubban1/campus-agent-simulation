"""Relationship state and evidence services."""

from app.social import repository


def get_relationship_score(conn, from_id, to_id):
    return repository.relationship_score(conn, from_id, to_id)


def change_relationship(conn, from_id, to_id, delta, note, *, clamp):
    next_score = clamp(get_relationship_score(conn, from_id, to_id) + delta)
    repository.upsert_relationship_score(conn, from_id, to_id, next_score, note)
    return next_score


def get_relationship_dynamics(conn, from_id, to_id, *, ensure_tables, relationship_score, clamp, current_day):
    ensure_tables(conn)
    row = repository.relationship_dynamics(conn, from_id, to_id)
    if not row:
        score = relationship_score(conn, from_id, to_id)
        repository.insert_relationship_dynamics(conn, (from_id, to_id, clamp(50 + score // 2), clamp(45 + score // 2), clamp(40 + score // 2), 0, 0, 0, current_day(conn)))
        row = repository.relationship_dynamics(conn, from_id, to_id)
    return dict(row)


def relationship_histories_by_target(conn, from_id, target_ids, per_target=12):
    ids = sorted({int(target_id) for target_id in target_ids if target_id is not None})
    if not ids:
        return {}
    rows = repository.relationship_histories(conn, from_id, ids, max(1, min(int(per_target), 20)))
    grouped = {target_id: [] for target_id in ids}
    for row in rows:
        grouped.setdefault(int(row["to_resident_id"]), []).append(row)
    return grouped


def record_social_relation_interpretation(conn, from_id, to_id, tick_id=None, perspective="system_researcher", *, ensure_tables, infer_relationship, current_day, json_dumps):
    ensure_tables(conn)
    interpretation = infer_relationship(conn, from_id, to_id)
    repository.insert_relation_interpretation(conn, (current_day(conn), tick_id, from_id, to_id, perspective, interpretation["label"], interpretation["confidence"], json_dumps(interpretation["candidates"], ensure_ascii=False), json_dumps(interpretation["evidence"], ensure_ascii=False), json_dumps(interpretation["metrics"], ensure_ascii=False), interpretation["interpretation_boundary"]))


def negotiate_between(conn, initiator_id, target_id, topic, proposal, *, ensure_tables, get_resident,
                      profile_meta, relationship_score, evolve_relationship, add_event,
                      current_day, record_learning, action_score, not_found):
    ensure_tables(conn)
    initiator, target = get_resident(conn, initiator_id), get_resident(conn, target_id)
    if not initiator or not target:
        raise not_found("Agent 不存在")
    initiator_profile, target_profile = profile_meta(conn, initiator_id), profile_meta(conn, target_id)
    relationship = relationship_score(conn, initiator_id, target_id)
    success = relationship + (int(initiator_profile["hierarchy_level"]) - int(target_profile["hierarchy_level"])) * 8 >= 25
    delta, status = (6, "达成初步共识") if success else (2, "保留分歧，等待更多条件")
    description = f"{initiator['name']} 与 {target['name']} 围绕「{topic}」协商：{proposal}。结果：{status}。"
    evolve_relationship(conn, initiator_id, target_id, "negotiation", f"协商议题：{topic}", delta, delta, 0 if success else 2)
    evolve_relationship(conn, target_id, initiator_id, "negotiation", f"回应协商：{topic}", max(1, delta - 1), max(1, delta - 1), 0 if success else 2)
    add_event(conn, current_day(conn), "negotiation", description)
    for resident_id, lesson in ((initiator_id, f"围绕「{topic}」协商，学会根据关系和层级调整提案。"), (target_id, f"回应「{topic}」协商，形成对合作条件的判断。")):
        record_learning(conn, resident_id, "negotiate", status, action_score("negotiate", success), lesson)
    conn.commit()
    return {"type": "negotiation", "success": success, "status": status, "relationship_after": relationship_score(conn, initiator_id, target_id), "description": description}


def create_collaboration(conn, leader_id, member_ids, title, goal, *, ensure_tables, json_dumps,
                         current_day, evolve_relationship, record_learning, action_score,
                         add_event, not_found):
    ensure_tables(conn)
    member_ids = [leader_id] + [member_id for member_id in member_ids if member_id != leader_id]
    residents = repository.residents_by_ids(conn, member_ids)
    if len(residents) != len(set(member_ids)):
        raise not_found("有 Agent 不存在")
    score = 10 + len(member_ids) * 3
    repository.insert_collaboration(conn, (title, leader_id, json_dumps(member_ids, ensure_ascii=False), goal, "active", score))
    roles = {str(member_id): ("负责人" if member_id == leader_id else "成员") for member_id in member_ids}
    group = repository.insert_collaboration_group(conn, (title, "协作小组", leader_id, json_dumps(member_ids, ensure_ascii=False), json_dumps(roles, ensure_ascii=False), goal, current_day(conn) + 10, "成员按各自任务推进，并在每日模拟后汇总进度。"))
    for from_id in member_ids:
        for to_id in member_ids:
            if from_id != to_id:
                evolve_relationship(conn, from_id, to_id, "collaboration", f"参与协作：{title}", 4, 5, -1)
        record_learning(conn, from_id, "collaborate", "加入协作", action_score("collaborate", True), f"参与「{title}」，围绕「{goal}」分工合作。")
    add_event(conn, current_day(conn), "collaboration", f"协作项目「{title}」启动，目标：{goal}。")
    conn.commit()
    return {"type": "collaboration", "title": title, "leader_id": leader_id, "member_ids": member_ids, "goal": goal, "score": score, "status": "active", "group_goal_id": group.lastrowid}


def record_group_membership_event(conn, group_id, resident_id, action, reason, member_ids, *, current_day, json_dumps):
    repository.insert_group_membership_event(conn, (current_day(conn), group_id, resident_id, action, reason or "", json_dumps(member_ids, ensure_ascii=False)))


def change_group_membership(conn, resident_id, group_id, action, *, ensure_tables, load_json, json_dumps,
                            record_membership, evolve_relationship, add_event, current_day):
    ensure_tables(conn)
    group = repository.active_group(conn, group_id)
    if not group:
        raise ValueError("没有可加入的活跃小组" if action == "join" else "没有可退出的活跃小组")
    members, roles = load_json(group["member_ids"], []), load_json(group["roles"], {})
    if action == "join":
        if resident_id in members:
            return {"group_id": group_id, "message": "已经是该小组成员"}
        members.append(resident_id); roles[str(resident_id)] = "成员"
        reason, event_type, event_text = f"加入群体：{group['name']}", "group_join", f"Agent {resident_id} 加入小组「{group['name']}」。"
    else:
        if resident_id not in members:
            raise ValueError("当前不是该小组成员")
        if int(group["leader_id"]) == resident_id:
            raise ValueError("负责人不能直接退出，请先由小组重新选择负责人")
        members.remove(resident_id); roles.pop(str(resident_id), None)
        reason, event_type, event_text = f"离开群体：{group['name']}", "group_leave", f"Agent {resident_id} 退出小组「{group['name']}」。"
    repository.update_group_members(conn, group_id, json_dumps(members, ensure_ascii=False), json_dumps(roles, ensure_ascii=False))
    record_membership(conn, group_id, resident_id, action, reason, members)
    if action == "join":
        for member_id in members:
            if member_id != resident_id:
                evolve_relationship(conn, resident_id, member_id, "group_join", f"加入小组：{group['name']}", 2, 3, -1)
                evolve_relationship(conn, member_id, resident_id, "group_join", f"新成员加入：{group['name']}", 1, 2, 0)
    add_event(conn, current_day(conn), event_type, event_text)
    return {"group_id": group_id, "group_name": group["name"], "member_ids": members, "message": "加入小组成功" if action == "join" else "退出小组成功"}


def create_competition(conn, participant_ids, title, metric, *, ensure_tables, load_json, json_dumps,
                       random_int, record_learning, action_score, evolve_relationship, add_event,
                       current_day, bad_request, not_found):
    ensure_tables(conn)
    if len(participant_ids) < 2:
        raise bad_request("竞争至少需要 2 个 Agent")
    rows = repository.competition_participants(conn, participant_ids)
    if len(rows) != len(set(participant_ids)):
        raise not_found("有 Agent 不存在")
    scores = []
    for row in rows:
        skills = load_json(row["skills"], {})
        compete_skill = skills.get("compete", {}) if isinstance(skills, dict) else {}
        skill_score = compete_skill.get("score", 0) if isinstance(compete_skill, dict) else 0
        scores.append({"id": row["id"], "name": row["name"], "score": int(row["energy"]) + int(row["money"]) // 10 + int(skill_score) + random_int(0, 12)})
    scores.sort(key=lambda item: item["score"], reverse=True)
    winner = scores[0]
    result = f"{winner['name']} 在「{title}」中以 {winner['score']} 分暂时领先，评价指标：{metric}。"
    repository.insert_competition(conn, (title, json_dumps(participant_ids, ensure_ascii=False), metric, winner["id"], result))
    for item in scores:
        won = item["id"] == winner["id"]
        record_learning(conn, item["id"], "compete", "获胜" if won else "参与竞争", action_score("compete", won), f"参与「{title}」竞争，理解自身在「{metric}」上的优势和差距。")
        for opponent in scores:
            if opponent["id"] != item["id"]:
                evolve_relationship(conn, item["id"], opponent["id"], "competition", f"参与竞争：{title}", 1 if won else 0, 0, 3)
    add_event(conn, current_day(conn), "competition", result)
    conn.commit()
    return {"type": "competition", "title": title, "metric": metric, "winner_id": winner["id"], "scores": scores, "result": result}
