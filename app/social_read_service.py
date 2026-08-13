"""Read models for Agent social activity."""


def build_profile_activity(
    conn,
    resident_id,
    *,
    ensure_tables,
    fetch_timeline,
    fetch_logs,
    build_social_graph,
    timeline_limit=20,
):
    ensure_tables(conn)
    page_size = min(max(timeline_limit, 1), 40)
    timeline_rows = fetch_timeline(conn, resident_id, limit=page_size + 1, offset=0)
    latest_logs = fetch_logs(conn, resident_id, limit=1)
    return {
        "social_graph": build_social_graph(conn, resident_id, limit=10),
        "timeline": timeline_rows[:page_size],
        "timeline_has_more": len(timeline_rows) > page_size,
        "latest_simulation_log": latest_logs[0] if latest_logs else None,
    }


def list_organizations(conn, *, ensure_tables, load_json):
    ensure_tables(conn)
    rows = conn.execute(
        """
        SELECT campus_organizations.*,
               COUNT(organization_members.resident_id) AS active_members
        FROM campus_organizations
        LEFT JOIN organization_members
          ON organization_members.organization_id = campus_organizations.id
         AND organization_members.status = 'active'
        GROUP BY campus_organizations.id
        ORDER BY campus_organizations.organization_type, campus_organizations.id
        """
    ).fetchall()
    organizations = []
    for row in rows:
        item = dict(row)
        item["resources"] = load_json(item["resources"], {})
        item["schedule"] = load_json(item["schedule"], [])
        organizations.append(item)
    return organizations


def list_group_goals(conn, *, ensure_tables, rows_to_dicts):
    ensure_tables(conn)
    rows = conn.execute(
        "SELECT * FROM group_goals ORDER BY status, deadline_day, id DESC"
    ).fetchall()
    return rows_to_dicts(rows)
