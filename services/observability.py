"""Calibration and observer-session application services."""


def current_metric_value(conn, metric_name, location, *, current_day, campus_environment):
    day = current_day(conn)
    environment = dict(campus_environment(conn, day))
    if metric_name in environment:
        return float(environment.get(metric_name) or 0)
    if metric_name == "agent_count" and location:
        row = conn.execute("SELECT COUNT(*) AS value FROM residents WHERE location = ?", (location,)).fetchone()
        return float(row["value"] if row else 0)
    if metric_name == "action_count":
        row = conn.execute("SELECT COUNT(*) AS value FROM simulation_action_logs WHERE day = ?", (day,)).fetchone()
        return float(row["value"] if row else 0)
    return None


def create_calibration_observation(conn, payload, *, world_now, json_dumps):
    cursor = conn.execute(
        """INSERT INTO calibration_observations
           (source_name, observed_at, metric_name, metric_value, location, role_group, sample_size, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (payload.source_name, payload.observed_at or world_now().isoformat(), payload.metric_name,
         payload.metric_value, payload.location, payload.role_group, payload.sample_size,
         json_dumps(payload.metadata, ensure_ascii=False)),
    )
    row = conn.execute("SELECT * FROM calibration_observations WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return {"observation": dict(row)}


def calibration_report(conn, *, metric_value, world_now, json_dumps):
    rows = conn.execute("SELECT * FROM calibration_observations ORDER BY id DESC LIMIT 100").fetchall()
    comparisons = []
    for row in rows:
        simulated = metric_value(conn, row["metric_name"], row["location"])
        if simulated is None:
            continue
        observed = float(row["metric_value"])
        delta = simulated - observed
        comparisons.append({"metric_name": row["metric_name"], "location": row["location"],
                            "observed": observed, "simulated": simulated, "delta": delta,
                            "relative_error": round(abs(delta) / max(1.0, abs(observed)), 3)})
    mean_error = round(sum(item["relative_error"] for item in comparisons) / len(comparisons), 3) if comparisons else None
    summary = "暂无可比较校准观测。" if mean_error is None else f"最近 {len(comparisons)} 条可比较观测的平均相对误差为 {mean_error}。"
    cursor = conn.execute(
        "INSERT INTO calibration_reports (report_key, summary, parameter_updates, quality_report_json) VALUES (?, ?, ?, ?)",
        (f"calibration-{world_now().strftime('%Y%m%d%H%M%S')}", summary, json_dumps({}, ensure_ascii=False),
         json_dumps({"mean_relative_error": mean_error, "comparisons": comparisons[:40]}, ensure_ascii=False)),
    )
    return {"report_id": cursor.lastrowid, "summary": summary, "mean_relative_error": mean_error, "comparisons": comparisons}


def upsert_observer_session(conn, payload, *, world_now):
    now = world_now().isoformat()
    session_id = None
    if payload.session_id:
        existing = conn.execute("SELECT * FROM observer_sessions WHERE id = ?", (payload.session_id,)).fetchone()
        if existing:
            conn.execute(
                """UPDATE observer_sessions SET user_id = ?, session_type = ?, focused_resident_id = ?,
                   focused_location = ?, last_seen_at = ? WHERE id = ?""",
                (payload.user_id, payload.session_type, payload.focused_resident_id, payload.focused_location, now, payload.session_id),
            )
            session_id = payload.session_id
    if not session_id:
        cursor = conn.execute(
            """INSERT INTO observer_sessions
               (user_id, session_type, focused_resident_id, focused_location, started_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (payload.user_id, payload.session_type, payload.focused_resident_id, payload.focused_location, now, now),
        )
        session_id = cursor.lastrowid
    row = conn.execute("SELECT * FROM observer_sessions WHERE id = ?", (session_id,)).fetchone()
    return {"session": dict(row), "event": None}
