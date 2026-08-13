"""Goal-domain database access."""


def residents_without_long_term_goal(conn):
    return conn.execute("""SELECT r.id, r.goal FROM residents r
        WHERE NOT EXISTS (SELECT 1 FROM long_term_goals g WHERE g.resident_id = r.id)""").fetchall()


def insert_long_term_goal(conn, resident_id, title, category, deadline_day, current_day):
    conn.execute("INSERT INTO long_term_goals (resident_id, title, category, deadline_day, last_update_day) VALUES (?, ?, ?, ?, ?)", (resident_id, title, category, deadline_day, current_day))


def legacy_long_term_goals(conn):
    return conn.execute("SELECT id, resident_id, title, category, progress, deadline_day, status, last_update_day, created_at, completed_at FROM long_term_goals ORDER BY id").fetchall()


def insert_legacy_multiscale_goal(conn, row):
    conn.execute("""INSERT INTO agent_goals (resident_id, legacy_long_term_goal_id, horizon, title, category, source, priority, commitment, expected_utility, feasibility, uncertainty, deadline_at, status, progress, visibility, created_day, last_reviewed_day, created_at, completed_at)
        VALUES (?, ?, 'long', ?, ?, 'legacy_migration', 70, 65, 70, 55, 35, ?, ?, ?, 'private', 1, ?, ?, ?)
        ON CONFLICT(legacy_long_term_goal_id) DO NOTHING""", (row["resident_id"], row["id"], row["title"], row["category"], f"simulation-day:{row['deadline_day']}", row["status"], row["progress"], row["last_update_day"], row["created_at"], row["completed_at"]))


def insert_goal_revision(conn, values):
    conn.execute("""INSERT INTO goal_revisions (goal_id, resident_id, day, tick_id, revision_type, before_json, after_json, reason, trigger_type, evidence_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values)


def trajectory_episode(conn, resident_id, goal_id, horizon):
    return conn.execute("SELECT * FROM trajectory_episodes WHERE resident_id = ? AND goal_id = ? AND horizon = ?", (resident_id, goal_id, horizon)).fetchone()


def create_trajectory_episode(conn, values):
    return conn.execute("""INSERT INTO trajectory_episodes (resident_id, goal_id, horizon, episode_type, title, start_at, status, planned_summary, evidence_json)
        VALUES (?, ?, ?, 'goal_pursuit', ?, ?, 'active', ?, '{}') ON CONFLICT(resident_id, goal_id, horizon) DO NOTHING""", values)
