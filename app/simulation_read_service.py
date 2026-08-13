"""Read models for simulation traces."""


def fetch_simulation_logs(conn, resident_id, *, limit=12, load_json):
    rows = conn.execute(
        """
        SELECT day, perception, retrieved_memories, decision, execution, environment_feedback, created_at
        FROM simulation_action_logs
        WHERE resident_id = ?
        ORDER BY id DESC LIMIT ?
        """,
        (resident_id, min(max(limit, 1), 50)),
    ).fetchall()
    return [
        {
            "day": row["day"],
            "perception": load_json(row["perception"], {}),
            "retrieved_memories": load_json(row["retrieved_memories"], []),
            "decision": load_json(row["decision"], {}),
            "execution": load_json(row["execution"], {}),
            "environment_feedback": load_json(row["environment_feedback"], {}),
            "created_at": row["created_at"],
        }
        for row in rows
    ]
