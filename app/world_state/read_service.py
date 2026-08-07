"""Read services for snapshots and branches."""


def list_branches(conn, *, decode_branch):
    rows = conn.execute("SELECT * FROM world_branches ORDER BY id").fetchall()
    return {"branches": [decode_branch(row) for row in rows]}


def get_snapshot(conn, snapshot_id, *, include_state, decode_snapshot):
    row = conn.execute(
        "SELECT * FROM world_snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()
    if not row:
        return None
    return {"snapshot": decode_snapshot(row, include_state=include_state)}
