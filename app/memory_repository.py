"""Database access for Agent memory and recent event read models."""


def recent_events(conn, day, limit):
    return conn.execute(
        """SELECT day, event_type, description, created_at FROM city_events
           WHERE day <= ? ORDER BY id DESC LIMIT ?""",
        (day, limit),
    ).fetchall()


def memory_page(conn, resident_id, day, limit, offset):
    total = conn.execute(
        "SELECT COUNT(*) AS total FROM memories WHERE resident_id = ? AND day <= ?",
        (resident_id, day),
    ).fetchone()["total"]
    rows = conn.execute(
        """SELECT id, day, content, importance, memory_type, tags, source,
                  access_count, last_accessed_at, created_at
           FROM memories WHERE resident_id = ? AND day <= ?
           ORDER BY id DESC LIMIT ? OFFSET ?""",
        (resident_id, day, limit, offset),
    ).fetchall()
    return total, rows
