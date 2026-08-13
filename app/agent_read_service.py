"""Read services for Agent goals, learning, and relationships."""


def list_agent_learning(conn, resident_id, *, ensure_tables, rows_to_dicts):
    ensure_tables(conn)
    rows = conn.execute(
        """
        SELECT day, action, outcome, score_delta, lesson, created_at
        FROM agent_learning
        WHERE resident_id = ?
        ORDER BY id DESC
        LIMIT 30
        """,
        (resident_id,),
    ).fetchall()
    return {"resident_id": resident_id, "learning": rows_to_dicts(rows)}


def list_long_term_goals(conn, resident_id, *, ensure_tables, rows_to_dicts):
    ensure_tables(conn)
    rows = conn.execute(
        "SELECT * FROM long_term_goals WHERE resident_id = ? ORDER BY status, deadline_day, id",
        (resident_id,),
    ).fetchall()
    return rows_to_dicts(rows)


def list_relationships(
    conn,
    resident_id,
    *,
    ensure_tables,
    get_relationship_dynamics,
):
    ensure_tables(conn)
    rows = conn.execute(
        """
        SELECT relationships.to_resident_id, residents.name, residents.role,
               relationships.score, relationships.notes
        FROM relationships JOIN residents ON residents.id = relationships.to_resident_id
        WHERE relationships.from_resident_id = ?
        ORDER BY relationships.score DESC
        """,
        (resident_id,),
    ).fetchall()
    relationships = []
    for row in rows:
        item = dict(row)
        item["dynamics"] = get_relationship_dynamics(conn, resident_id, item["to_resident_id"])
        relationships.append(item)
    return relationships


def build_social_hierarchy(conn, *, ensure_tables, get_hierarchy_title):
    ensure_tables(conn)
    rows = conn.execute(
        """
        SELECT residents.id, residents.name, residents.role,
               agent_profiles.organization, agent_profiles.hierarchy_level
        FROM residents
        JOIN agent_profiles ON agent_profiles.resident_id = residents.id
        ORDER BY agent_profiles.hierarchy_level DESC, residents.id
        """
    ).fetchall()
    levels = {}
    for row in rows:
        level = int(row["hierarchy_level"])
        levels.setdefault(
            str(level), {"title": get_hierarchy_title(level), "agents": []}
        )["agents"].append(dict(row))
    return {"levels": levels}


def build_goal_system(
    conn,
    resident_id,
    *,
    resident,
    ensure_tables,
    rows_to_dicts,
    load_json,
):
    ensure_tables(conn)
    profile = conn.execute(
        "SELECT strategy, energy, mood, current_task FROM agent_profiles WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    goals = rows_to_dicts(conn.execute(
        """SELECT * FROM agent_goals WHERE resident_id = ?
        ORDER BY CASE horizon WHEN 'long' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                 status, priority DESC, id""", (resident_id,)
    ).fetchall())
    by_parent = {}
    for goal in goals:
        by_parent.setdefault(goal.get("parent_goal_id"), []).append(goal)

    def goal_node(goal):
        item = dict(goal)
        item["children"] = [goal_node(child) for child in by_parent.get(goal["id"], [])]
        return item

    def rows(sql, params=()):
        return rows_to_dicts(conn.execute(sql, params).fetchall())

    dependencies = rows("SELECT * FROM goal_dependencies WHERE goal_id IN (SELECT id FROM agent_goals WHERE resident_id = ?) ORDER BY id", (resident_id,))
    commitments = rows("SELECT * FROM agent_commitments WHERE resident_id = ? ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, due_at DESC, id DESC LIMIT 40", (resident_id,))
    revisions = rows("SELECT * FROM goal_revisions WHERE resident_id = ? ORDER BY id DESC LIMIT 60", (resident_id,))
    outcomes = rows("SELECT * FROM plan_outcomes WHERE resident_id = ? ORDER BY id DESC LIMIT 60", (resident_id,))
    trajectories = rows("SELECT * FROM trajectory_episodes WHERE resident_id = ? ORDER BY CASE horizon WHEN 'long' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, id DESC", (resident_id,))
    plan_row = conn.execute("SELECT * FROM agent_action_plans WHERE resident_id = ? AND status = 'active' ORDER BY window_start DESC LIMIT 1", (resident_id,)).fetchone()
    current_plan = dict(plan_row) if plan_row else None
    if current_plan:
        current_plan["plan"] = load_json(current_plan.pop("plan_json"), {})
    strategy = load_json(profile["strategy"], {}) if profile else {}
    return {
        "version": "multiscale-goals-v1", "resident": dict(resident),
        "stable_layer": {"personality": resident["personality"], "role": resident["role"], "money": resident["money"], "energy": profile["energy"] if profile else None, "mood": profile["mood"] if profile else "", "current_task": profile["current_task"] if profile else "", "personality_traits": strategy.get("personality_traits", {}) if isinstance(strategy, dict) else {}},
        "goal_tree": [goal_node(goal) for goal in by_parent.get(None, [])], "goals": goals,
        "dependencies": dependencies, "commitments": commitments, "current_plan": current_plan,
        "recent_outcomes": outcomes, "goal_revisions": revisions, "trajectory_episodes": trajectories,
    }
