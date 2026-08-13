"""Campus environment database access."""


def campus_state(conn, day):
    return conn.execute("SELECT * FROM campus_state WHERE day = ?", (day,)).fetchone()



def latest_campus_state_before(conn, day):
    return conn.execute("SELECT * FROM campus_state WHERE day < ? ORDER BY day DESC LIMIT 1", (day,)).fetchone()


def active_campus_events(conn, day):
    return conn.execute("SELECT * FROM campus_events WHERE day = ? AND status = 'active' ORDER BY id DESC", (day,)).fetchall()


def update_campus_state(conn, day, updates):
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    conn.execute(f"UPDATE campus_state SET {set_clause} WHERE day = ?", list(updates.values()) + [day])


def upsert_campus_state(conn, day, values):
    columns = list(values)
    assignments = ", ".join(f"{column} = excluded.{column}" for column in columns)
    placeholders = ", ".join(["?"] * (len(columns) + 1))
    conn.execute(f"INSERT INTO campus_state (day, {', '.join(columns)}) VALUES ({placeholders}) ON CONFLICT(day) DO UPDATE SET {assignments}", [day] + [values[column] for column in columns])


def ensure_campus_state(conn, day, values):
    columns = list(values)
    placeholders = ", ".join(["?"] * (len(columns) + 1))
    conn.execute(f"INSERT INTO campus_state (day, {', '.join(columns)}) VALUES ({placeholders}) ON CONFLICT(day) DO NOTHING", [day] + [values[column] for column in columns])


def insert_campus_event(conn, values):
    return conn.execute("INSERT INTO campus_events (day, title, event_type, intensity, target_spaces, effects) VALUES (?, ?, ?, ?, ?, ?)", values)


def insert_default_config(conn, config_json, checksum):
    conn.execute("""INSERT OR IGNORE INTO environment_configs (config_key, name, version, status, config_json, checksum, created_by)
        VALUES ('campus-default', '默认校园平行世界', 1, 'active', ?, ?, 'system')""", (config_json, checksum))
    return conn.execute("SELECT * FROM environment_configs WHERE config_key = 'campus-default' AND version = 1").fetchone()


def active_config(conn, runtime_id):
    row = conn.execute("SELECT c.* FROM environment_configs c JOIN world_runtime w ON w.environment_config_id = c.id WHERE w.id = ?", (runtime_id,)).fetchone()
    return row or conn.execute("SELECT * FROM environment_configs WHERE status = 'active' ORDER BY id DESC LIMIT 1").fetchone()


def config_parent_exists(conn, config_id):
    return conn.execute("SELECT id FROM environment_configs WHERE id = ?", (config_id,)).fetchone()


def next_config_version(conn, config_key):
    return int(conn.execute("SELECT COALESCE(MAX(version), 0) AS value FROM environment_configs WHERE config_key = ?", (config_key,)).fetchone()["value"] or 0) + 1


def insert_config(conn, values):
    cursor = conn.execute("""INSERT INTO environment_configs (config_key, name, version, parent_config_id, status, config_json, checksum, created_by)
        VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)""", values)
    return conn.execute("SELECT * FROM environment_configs WHERE id = ?", (cursor.lastrowid,)).fetchone()
