from __future__ import annotations

import json
from math import dist


IGNORED_EVENT_TYPES = {
    "world_tick_started",
    "world_tick_complete",
    "observer_session",
    "observer_model_detail",
}


def _json_value(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def perception_runtime_available(conn):
    return bool(conn.execute("PRAGMA table_info(agent_observations)").fetchall())


def _node_maps(conn):
    by_id = {}
    by_location = {}
    for row in conn.execute(
        "SELECT id, code, name, x, y, z, properties FROM spatial_nodes"
    ).fetchall():
        item = dict(row)
        item["properties"] = _json_value(item.get("properties"), {})
        by_id[int(item["id"])] = item
        for key in {
            item["code"],
            item["name"],
            item["properties"].get("location"),
        }:
            if key:
                by_location[str(key)] = item
    return by_id, by_location


def _event_node(event, by_location, agent_nodes):
    if event.get("location") and event["location"] in by_location:
        return by_location[event["location"]]
    resident_id = event.get("resident_id")
    return agent_nodes.get(int(resident_id)) if resident_id else None


def _observation_characteristics(event_type, distance_meters, visual_radius, hearing_radius):
    social_sound = any(
        token in str(event_type)
        for token in ("chat", "conflict", "collaborate", "queue")
    )
    if social_sound and distance_meters <= hearing_radius:
        ratio = distance_meters / max(1.0, hearing_radius)
        return "auditory", round(92 - ratio * 28), round(2 + ratio * 12, 2)
    if distance_meters <= visual_radius:
        ratio = distance_meters / max(1.0, visual_radius)
        return "visual", round(96 - ratio * 24), round(1 + ratio * 8, 2)
    return None


def _memory_characteristics(event_type):
    event_type = str(event_type or "")
    if any(token in event_type for token in ("failed", "conflict", "abandoned", "closed")):
        return 82, -65
    if any(token in event_type for token in ("arrived", "completed", "chat", "collaborate")):
        return 68, 45
    return 48, 0


def _upsert_belief(conn, observation, observed_at):
    existing = conn.execute(
        """
        SELECT id, confidence, evidence_count
        FROM agent_belief_states
        WHERE resident_id = ? AND subject_type = ? AND subject_id = ?
          AND belief_type = ? AND branch_key = ?
        """,
        (
            observation["observer_resident_id"],
            observation["subject_type"],
            observation["subject_id"],
            observation["fact_type"],
            observation["branch_key"],
        ),
    ).fetchone()
    if existing:
        confidence = min(
            100,
            round(
                float(existing["confidence"]) * 0.65
                + float(observation["confidence"]) * 0.35
            ),
        )
        conn.execute(
            """
            UPDATE agent_belief_states
            SET summary = ?, confidence = ?, last_observation_id = ?,
                evidence_count = evidence_count + 1, status = 'active',
                last_updated_at = ?, metadata = ?
            WHERE id = ?
            """,
            (
                observation["summary"],
                confidence,
                observation["id"],
                observed_at,
                json.dumps({"latest_modality": observation["modality"]}),
                existing["id"],
            ),
        )
        return int(existing["id"])
    cursor = conn.execute(
        """
        INSERT INTO agent_belief_states
        (resident_id, subject_type, subject_id, belief_type, summary,
         confidence, last_observation_id, evidence_count, status, branch_key,
         first_formed_at, last_updated_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'active', ?, ?, ?, ?)
        RETURNING id
        """,
        (
            observation["observer_resident_id"],
            observation["subject_type"],
            observation["subject_id"],
            observation["fact_type"],
            observation["summary"],
            observation["confidence"],
            observation["id"],
            observation["branch_key"],
            observed_at,
            observed_at,
            json.dumps({"latest_modality": observation["modality"]}),
        ),
    )
    inserted = cursor.fetchone()
    return int(inserted["id"] if isinstance(inserted, dict) else inserted[0])


def _store_spatial_memory(conn, observation, observed_at):
    salience, valence = _memory_characteristics(observation["fact_type"])
    conn.execute(
        """
        INSERT OR IGNORE INTO agent_spatial_memories
        (resident_id, observation_id, node_id, memory_type, summary, salience,
         confidence, valence, visit_count, formed_at, branch_key, metadata)
        VALUES (?, ?, ?, 'episodic', ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            observation["observer_resident_id"],
            observation["id"],
            observation["origin_node_id"],
            observation["summary"],
            salience,
            observation["confidence"],
            valence,
            observed_at,
            observation["branch_key"],
            json.dumps(
                {
                    "source_event_id": observation["source_event_id"],
                    "modality": observation["modality"],
                }
            ),
        ),
    )


def _capture_current_physical_observations(conn, agent_rows, world_time, tick_id, branch_key):
    """Record only directly observed local conditions, never a world-wide feed."""
    try:
        states = {
            int(row["node_id"]): dict(row)
            for row in conn.execute(
                """
                SELECT node_id, crowd_density, noise_db, precipitation,
                       illumination, access_status, version, observed_at
                FROM spatial_physical_states
                """
            ).fetchall()
        }
    except Exception:
        return []

    captured = []
    observed_at = world_time.isoformat()
    for raw_agent in agent_rows:
        agent = dict(raw_agent)
        node_id = int(agent["current_node_id"])
        state = states.get(node_id)
        if not state:
            continue
        # Normal, quiet, open space needs no new cognitive record.  Physical
        # facts become memories only when they can influence a decision.
        crowd = float(state.get("crowd_density") or 0)
        noise = float(state.get("noise_db") or 0)
        rain = float(state.get("precipitation") or 0)
        access = str(state.get("access_status") or "open")
        if access == "open" and crowd < 0.35 and noise < 55 and rain <= 0:
            continue
        subject_id = f"node:{node_id}:physical:{state.get('version', 1)}"
        existing = conn.execute(
            """SELECT id FROM agent_observations
               WHERE observer_resident_id = ? AND subject_id = ?
                 AND fact_type = 'spatial_physical_state' AND branch_key = ?
               LIMIT 1""",
            (agent["resident_id"], subject_id, branch_key),
        ).fetchone()
        if existing:
            continue
        summary = (
            f"直接观察：当前位置物理状态为{access}；"
            f"拥挤度{round(crowd * 100)}%，噪声{round(noise)}dB，降水{rain:g}。"
        )
        inserted = conn.execute(
            """
            INSERT INTO agent_observations
            (observer_resident_id, tick_id, source_event_id, origin_node_id,
             subject_type, subject_id, modality, fact_type, summary,
             distance_meters, confidence, error_margin, observed_at,
             branch_key, metadata)
            VALUES (?, ?, NULL, ?, 'location', ?, 'direct',
                    'spatial_physical_state', ?, 0, 96, 1, ?, ?, ?)
            """,
            (
                agent["resident_id"], tick_id, node_id, subject_id, summary,
                observed_at, branch_key,
                json.dumps({"physical_state": state, "observation_scope": "current_node"}, default=str),
            ),
        ).rowcount
        if not inserted:
            continue
        observation_id = conn.execute(
            "SELECT id FROM agent_observations WHERE observer_resident_id = ? AND subject_id = ? AND branch_key = ?",
            (agent["resident_id"], subject_id, branch_key),
        ).fetchone()["id"]
        observation = {
            "id": int(observation_id), "observer_resident_id": int(agent["resident_id"]),
            "source_event_id": None, "origin_node_id": node_id,
            "subject_type": "location", "subject_id": subject_id,
            "modality": "direct", "fact_type": "spatial_physical_state",
            "summary": summary, "distance_meters": 0.0, "confidence": 96,
            "error_margin": 1.0, "branch_key": branch_key,
        }
        _upsert_belief(conn, observation, observed_at)
        _store_spatial_memory(conn, observation, observed_at)
        captured.append(observation)
    return captured


def capture_tick_observations(conn, world_time, tick_id, day, branch_key="main"):
    if not perception_runtime_available(conn):
        return []
    by_id, by_location = _node_maps(conn)
    agent_rows = conn.execute(
        """
        SELECT state.resident_id, state.current_node_id, state.x, state.y, state.z,
               capability.perception_radius_m, capability.hearing_radius_m,
               profile.information_literacy, profile.language_access
        FROM agent_spatial_states state
        JOIN agent_spatial_capabilities capability
          ON capability.resident_id = state.resident_id
        LEFT JOIN agent_capability_profiles profile
          ON profile.resident_id = state.resident_id
        ORDER BY state.resident_id
        """
    ).fetchall()
    agent_nodes = {
        int(row["resident_id"]): by_id.get(int(row["current_node_id"]))
        for row in agent_rows
    }
    source_events = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, tick_id, event_type, resident_id, location, title,
                   content, occurred_at
            FROM world_event_stream
            WHERE day = ? AND branch_key = ?
              AND (tick_id IS NULL OR tick_id < ?)
            ORDER BY id DESC
            LIMIT 40
            """,
            (day, branch_key, tick_id),
        ).fetchall()
        if row["event_type"] not in IGNORED_EVENT_TYPES
    ]
    captured = _capture_current_physical_observations(
        conn, agent_rows, world_time, tick_id, branch_key
    )
    for raw_agent in agent_rows:
        agent = dict(raw_agent)
        observer_id = int(agent["resident_id"])
        observer_position = (
            float(agent["x"]),
            float(agent["y"]),
            float(agent["z"]),
        )
        for event in source_events:
            event_node = _event_node(event, by_location, agent_nodes)
            if int(event.get("resident_id") or 0) == observer_id:
                modality, distance_meters, confidence, error_margin = (
                    "self",
                    0.0,
                    100,
                    0.0,
                )
            elif event_node:
                distance_meters = dist(
                    observer_position,
                    (
                        float(event_node["x"]),
                        float(event_node["y"]),
                        float(event_node["z"]),
                    ),
                )
                characteristics = _observation_characteristics(
                    event["event_type"],
                    distance_meters,
                    float(agent["perception_radius_m"]),
                    float(agent["hearing_radius_m"]),
                )
                if not characteristics:
                    continue
                modality, confidence, error_margin = characteristics
            else:
                continue
            information_literacy = int(agent.get("information_literacy") or 50)
            language_access = int(agent.get("language_access") or 50)
            if modality != "self":
                confidence_adjustment = (information_literacy - 50) * 0.18
                if modality == "auditory":
                    confidence_adjustment += (language_access - 50) * 0.08
                confidence = round(
                    max(1, min(100, confidence + confidence_adjustment))
                )
                error_margin = round(
                    error_margin
                    * max(0.55, min(1.45, 1 + (50 - information_literacy) / 100)),
                    2,
                )
            subject_type = "agent" if event.get("resident_id") else "location"
            subject_id = str(
                event.get("resident_id")
                or event.get("location")
                or event["event_type"]
            )
            inserted = conn.execute(
                """
                INSERT OR IGNORE INTO agent_observations
                (observer_resident_id, tick_id, source_event_id, origin_node_id,
                 subject_type, subject_id, modality, fact_type, summary,
                 distance_meters, confidence, error_margin, observed_at,
                 branch_key, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observer_id,
                    tick_id,
                    event["id"],
                    agent["current_node_id"],
                    subject_type,
                    subject_id,
                    modality,
                    event["event_type"],
                    f"{event['title']}：{event['content']}"[:600],
                    round(distance_meters, 3),
                    confidence,
                    error_margin,
                    world_time.isoformat(),
                    branch_key,
                    json.dumps(
                         {
                            "source_tick_id": event.get("tick_id"),
                           "event_location": event.get("location"),
                            "event_occurred_at": event.get("occurred_at"),
                            "capability_adjustment": {
                                "information_literacy": information_literacy,
                                "language_access": language_access,
                                "version": "capability-defaults-v1",
                            },
                        },
                        default=str,
                    ),
                ),
            ).rowcount
            if not inserted:
                continue
            observation_id = conn.execute(
                """
                SELECT id FROM agent_observations
                WHERE observer_resident_id = ? AND source_event_id = ?
                  AND modality = ?
                """,
                (observer_id, event["id"], modality),
            ).fetchone()["id"]
            observation = {
                "id": int(observation_id),
                "observer_resident_id": observer_id,
                "source_event_id": int(event["id"]),
                "origin_node_id": int(agent["current_node_id"]),
                "subject_type": subject_type,
                "subject_id": subject_id,
                "modality": modality,
                "fact_type": event["event_type"],
                "summary": f"{event['title']}：{event['content']}"[:600],
                "distance_meters": round(distance_meters, 3),
                "confidence": confidence,
                "error_margin": error_margin,
                "branch_key": branch_key,
            }
            _upsert_belief(conn, observation, world_time.isoformat())
            _store_spatial_memory(conn, observation, world_time.isoformat())
            captured.append(observation)
    return captured


def get_agent_cognitive_context(conn, resident_id, branch_key="main", limit=8):
    if not perception_runtime_available(conn):
        return {
            "observations": [],
            "beliefs": [],
            "spatial_memories": [],
            "received_information": [],
            "adaptive_memories": [],
            "strategy_states": [],
            "norm_beliefs": [],
        }
    observations = conn.execute(
        """
        SELECT observation.*, node.name AS origin_node_name
        FROM agent_observations observation
        LEFT JOIN spatial_nodes node ON node.id = observation.origin_node_id
        WHERE observation.observer_resident_id = ?
          AND observation.branch_key = ?
        ORDER BY observation.id DESC LIMIT ?
        """,
        (resident_id, branch_key, limit),
    ).fetchall()
    beliefs = conn.execute(
        """
        SELECT * FROM agent_belief_states
        WHERE resident_id = ? AND branch_key = ? AND status = 'active'
        ORDER BY last_updated_at DESC LIMIT ?
        """,
        (resident_id, branch_key, limit),
    ).fetchall()
    memories = conn.execute(
        """
        SELECT memory.*, node.name AS node_name
        FROM agent_spatial_memories memory
        LEFT JOIN spatial_nodes node ON node.id = memory.node_id
        WHERE memory.resident_id = ? AND memory.branch_key = ?
        ORDER BY memory.salience DESC, memory.id DESC LIMIT ?
        """,
        (resident_id, branch_key, limit),
    ).fetchall()
    information = []
    if conn.execute("PRAGMA table_info(agent_information)").fetchall():
        information = conn.execute(
            """
            SELECT info.title, info.category, delivery.channel,
                   delivery.credibility, delivery.distortion_note,
                   delivery.received_at
            FROM agent_information delivery
            JOIN external_information info ON info.id = delivery.information_id
            WHERE delivery.resident_id = ?
            ORDER BY delivery.received_at DESC LIMIT ?
            """,
            (resident_id, limit),
        ).fetchall()
    adaptive = {
        "adaptive_memories": [],
        "strategy_states": [],
        "norm_beliefs": [],
    }
    if conn.execute("PRAGMA table_info(adaptive_memories)").fetchall():
        from app.adaptation.learning import get_adaptive_cognitive_context

        adaptive = get_adaptive_cognitive_context(
            conn, resident_id, branch_key=branch_key, limit=limit
        )
    return {
        "observations": [dict(row) for row in observations],
        "beliefs": [dict(row) for row in beliefs],
        "spatial_memories": [dict(row) for row in memories],
        "received_information": [dict(row) for row in information],
        **adaptive,
    }


def spatial_memory_location_factors(conn, resident_id, branch_key="main"):
    if not perception_runtime_available(conn):
        return {}
    rows = conn.execute(
        """
        SELECT memory.salience, memory.valence, node.name, node.properties
        FROM agent_spatial_memories memory
        JOIN spatial_nodes node ON node.id = memory.node_id
        WHERE memory.resident_id = ? AND memory.branch_key = ?
        ORDER BY memory.id DESC LIMIT 80
        """,
        (resident_id, branch_key),
    ).fetchall()
    scores = {}
    for row in rows:
        properties = _json_value(row["properties"], {})
        location = properties.get("location") or row["name"]
        scores[location] = scores.get(location, 0.0) + (
            float(row["valence"]) * float(row["salience"]) / 100.0
        )
    return {
        location: round(max(0.5, min(1.35, 1.0 + score / 300.0)), 3)
        for location, score in scores.items()
    }
