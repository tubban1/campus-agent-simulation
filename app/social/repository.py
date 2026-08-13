"""Social-domain database access."""


def relationship_score(conn, from_id, to_id):
    row = conn.execute("SELECT score FROM relationships WHERE from_resident_id = ? AND to_resident_id = ?", (from_id, to_id)).fetchone()
    return int(row["score"]) if row else 0


def upsert_relationship_score(conn, from_id, to_id, score, note):
    conn.execute("""INSERT INTO relationships (from_resident_id, to_resident_id, score, notes) VALUES (?, ?, ?, ?)
        ON CONFLICT(from_resident_id, to_resident_id) DO UPDATE SET score = excluded.score, notes = excluded.notes""", (from_id, to_id, score, note))


def relationship_dynamics(conn, from_id, to_id):
    return conn.execute("SELECT * FROM relationship_dynamics WHERE from_resident_id = ? AND to_resident_id = ?", (from_id, to_id)).fetchone()


def insert_relationship_dynamics(conn, values):
    conn.execute("""INSERT INTO relationship_dynamics (from_resident_id, to_resident_id, affinity, trust, cooperation, competition, conflict, tension, last_day)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", values)


def active_group(conn, group_id):
    return conn.execute("SELECT * FROM group_goals WHERE id = ? AND status = 'active'", (group_id,)).fetchone()


def update_group_members(conn, group_id, member_ids_json, roles_json):
    conn.execute("UPDATE group_goals SET member_ids = ?, roles = ? WHERE id = ?", (member_ids_json, roles_json, group_id))


def insert_group_membership_event(conn, values):
    conn.execute("INSERT INTO group_membership_events (day, group_id, resident_id, action, reason, member_ids) VALUES (?, ?, ?, ?, ?, ?)", values)


def residents_by_ids(conn, resident_ids):
    placeholders = ",".join(["?"] * len(resident_ids))
    return conn.execute(f"SELECT id FROM residents WHERE id IN ({placeholders})", resident_ids).fetchall()


def insert_collaboration(conn, values):
    conn.execute("INSERT INTO collaborations (title, leader_id, member_ids, goal, status, score) VALUES (?, ?, ?, ?, ?, ?)", values)


def insert_collaboration_group(conn, values):
    return conn.execute("""INSERT INTO group_goals (name, group_type, leader_id, member_ids, roles, shared_goal, deadline_day, current_plan)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", values)


def competition_participants(conn, participant_ids):
    placeholders = ",".join(["?"] * len(participant_ids))
    return conn.execute(f"""SELECT residents.id, residents.name, residents.money, agent_profiles.energy, agent_profiles.skills
        FROM residents JOIN agent_profiles ON agent_profiles.resident_id = residents.id WHERE residents.id IN ({placeholders})""", participant_ids).fetchall()


def insert_competition(conn, values):
    conn.execute("INSERT INTO competitions (title, participant_ids, metric, winner_id, result) VALUES (?, ?, ?, ?, ?)", values)


def relationship_histories(conn, from_id, target_ids, per_target):
    placeholders = ",".join(["?"] * len(target_ids))
    return conn.execute(f"""SELECT * FROM (
        SELECT to_resident_id, interaction, reason, affinity_before, affinity_after, trust_before, trust_after,
               cooperation_before, cooperation_after, competition_before, competition_after, conflict_before,
               conflict_after, day, created_at, ROW_NUMBER() OVER (PARTITION BY to_resident_id ORDER BY id DESC) AS history_rank
        FROM relationship_change_events WHERE from_resident_id = ? AND to_resident_id IN ({placeholders})
    ) ranked WHERE history_rank <= ? ORDER BY to_resident_id, history_rank""", (from_id, *target_ids, per_target)).fetchall()


def insert_relation_interpretation(conn, values):
    conn.execute("""INSERT INTO social_relation_interpretations (day, tick_id, from_resident_id, to_resident_id, perspective, current_label, label_confidence, candidate_labels_json, evidence_json, metrics_json, interpretation_boundary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values)
