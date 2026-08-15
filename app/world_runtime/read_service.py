"""Read services for world runtime data."""


def list_world_events(
    conn,
    *,
    after_id=0,
    limit=50,
    branch_key="",
    active_branch_key,
    decode_world_event,
):
    limit = max(1, min(limit, 200))
    selected_branch = branch_key or active_branch_key(conn)
    if after_id <= 0:
        rows = conn.execute(
            """
            SELECT e.*, r.name AS resident_name, r.role AS resident_role
            FROM world_event_stream e
            LEFT JOIN residents r ON r.id = e.resident_id
            WHERE e.branch_key = ?
            ORDER BY e.id DESC
            LIMIT ?
            """,
            (selected_branch, limit),
        ).fetchall()
        rows = list(reversed(rows))
    else:
        rows = conn.execute(
            """
            SELECT e.*, r.name AS resident_name, r.role AS resident_role
            FROM world_event_stream e
            LEFT JOIN residents r ON r.id = e.resident_id
            WHERE e.id > ? AND e.branch_key = ?
            ORDER BY e.id ASC
            LIMIT ?
            """,
            (after_id, selected_branch, limit),
        ).fetchall()
    events = [decode_world_event(row) for row in rows]
    return {
        "events": events,
        "next_after_id": events[-1]["id"] if events else after_id,
        "branch_key": selected_branch,
    }


def list_action_rules(conn, *, decode_action_rule):
    rows = conn.execute(
        """
        SELECT * FROM world_action_rules
        WHERE status = 'active'
        ORDER BY action_type
        """
    ).fetchall()
    return {"action_rules": [decode_action_rule(row) for row in rows]}


def list_action_executions(conn, *, resident_id=None, status="", limit=50, load_json):
    limit = max(1, min(limit, 200))
    rows = conn.execute(
        """
        SELECT * FROM world_action_executions
        WHERE (? IS NULL OR resident_id = ?)
          AND (? = '' OR status = ?)
        ORDER BY id DESC
        LIMIT ?
        """,
        (resident_id, resident_id, status, status, limit),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        for key, fallback in (
            ("precondition_results_json", []),
            ("resources_before_json", {}),
            ("resources_after_json", {}),
            ("resource_costs_json", {}),
            ("direct_effects_json", []),
            ("delayed_effect_ids_json", []),
        ):
            item[key.removesuffix("_json")] = load_json(item.pop(key, ""), fallback)
        items.append(item)
    return {"action_executions": items}


def list_delayed_effects(conn, *, status="", limit=50, load_json):
    limit = max(1, min(limit, 200))
    rows = conn.execute(
        """
        SELECT * FROM world_delayed_effects
        WHERE (? = '' OR status = ?)
        ORDER BY id DESC
        LIMIT ?
        """,
        (status, status, limit),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["value"] = load_json(item.pop("value_json", ""), None)
        items.append(item)
    return {"delayed_effects": items}
