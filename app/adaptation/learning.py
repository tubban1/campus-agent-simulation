from __future__ import annotations

import json
from app.json_utils import json_dumps
from datetime import datetime, timezone
from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


def _json(value):
    return json_dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _table_exists(conn, table_name):
    return bool(conn.execute(f"PRAGMA table_info({table_name})").fetchall())


def learning_runtime_available(conn):
    return _table_exists(conn, "adaptive_memories")


def _event_valence(event_type, payload):
    if any(token in event_type for token in ("failed", "conflict", "sanction", "injury", "abandoned")):
        return -55
    if any(token in event_type for token in ("arrived", "success", "reward", "completed", "collabor")):
        return 45
    if payload.get("success") is False:
        return -35
    if payload.get("success") is True:
        return 30
    return 0


def _event_salience(event_type, payload):
    value = 38
    if any(token in event_type for token in ("sanction", "injury", "conflict", "bypass")):
        value += 32
    if any(token in event_type for token in ("success", "completed", "arrived")):
        value += 15
    if payload.get("observed"):
        value += 8
    return min(100, value)


def _memory_type(event_type):
    if "relationship" in event_type or event_type in {"world_agent_chat", "world_agent_collaborate"}:
        return "relationship"
    if "boundary" in event_type or "admission" in event_type:
        return "strategy"
    return "episodic"


def _upsert_experience(
    conn,
    *,
    experience_key,
    branch_key,
    tick_number,
    resident_id,
    source_type,
    source_id,
    event_type,
    summary,
    outcome,
    location,
    occurred_at,
    evidence,
):
    conn.execute(
        """
        INSERT OR IGNORE INTO experience_records
        (experience_key, branch_key, tick_number, resident_id, source_type,
         source_id, event_type, objective_summary, outcome, location,
         occurred_at, evidence_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            experience_key,
            branch_key,
            tick_number,
            resident_id,
            source_type,
            str(source_id),
            event_type,
            summary[:800],
            outcome,
            location or "",
            occurred_at,
            _json(evidence),
        ),
    )
    return conn.execute(
        "SELECT * FROM experience_records WHERE experience_key = ?",
        (experience_key,),
    ).fetchone()


def _create_memory(conn, experience, event_type, summary, payload, occurred_at):
    key = f"experience:{experience['id']}:memory"
    valence = _event_valence(event_type, payload)
    salience = _event_salience(event_type, payload)
    confidence = max(0.35, min(1.0, float(payload.get("confidence", 0.82))))
    interpretation = summary
    if valence < 0:
        interpretation = f"这次经历提示风险或代价：{summary}"
    elif valence > 0:
        interpretation = f"这次经历证明某种做法可能有效：{summary}"
    conn.execute(
        """
        INSERT OR IGNORE INTO adaptive_memories
        (memory_key, resident_id, experience_id, memory_type, interpretation,
         confidence, salience, valence, strength, last_reinforced_at,
         metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            key,
            experience["resident_id"],
            experience["id"],
            _memory_type(event_type),
            interpretation[:1000],
            confidence,
            salience,
            valence,
            occurred_at,
            _json({"source_event_type": event_type}),
        ),
    )
    return conn.execute(
        "SELECT * FROM adaptive_memories WHERE memory_key = ?", (key,)
    ).fetchone()


def _learn_boundary_strategy(conn, experience, memory, payload, occurred_at):
    attempt = payload.get("boundary_attempt") or payload.get("attempt") or {}
    if not attempt:
        return None
    strategy = str(attempt.get("strategy") or "queue")
    context_key = f"space:{payload.get('target_node_id') or payload.get('target_key') or 'unknown'}"
    succeeded = bool(attempt.get("succeeded"))
    detected = bool(attempt.get("detected"))
    harmed = bool(attempt.get("harmed"))
    reward = (1.0 if succeeded else -0.65) - (0.45 if detected else 0) - (0.55 if harmed else 0)
    existing = conn.execute(
        """
        SELECT * FROM strategy_states
        WHERE resident_id = ? AND strategy_key = ? AND context_key = ?
        """,
        (experience["resident_id"], strategy, context_key),
    ).fetchone()
    before = dict(existing) if existing else {
        "expected_utility": 0,
        "confidence": 0.25,
        "success_count": 0,
        "failure_count": 0,
        "observation_count": 0,
    }
    observations = int(before["observation_count"]) + 1
    utility = round(float(before["expected_utility"]) * 0.7 + reward * 0.3, 4)
    confidence = round(min(0.95, 0.25 + observations * 0.08), 4)
    success_count = int(before["success_count"]) + int(succeeded)
    failure_count = int(before["failure_count"]) + int(not succeeded)
    conn.execute(
        """
        INSERT INTO strategy_states
        (resident_id, strategy_key, context_key, expected_utility, confidence,
         success_count, failure_count, observation_count, learned_at,
         last_updated_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(resident_id, strategy_key, context_key) DO UPDATE SET
            expected_utility = excluded.expected_utility,
            confidence = excluded.confidence,
            success_count = excluded.success_count,
            failure_count = excluded.failure_count,
            observation_count = excluded.observation_count,
            last_updated_at = excluded.last_updated_at,
            metadata_json = excluded.metadata_json
        """,
        (
            experience["resident_id"],
            strategy,
            context_key,
            utility,
            confidence,
            success_count,
            failure_count,
            observations,
            occurred_at,
            occurred_at,
            _json({"last_attempt": attempt}),
        ),
    )
    after = dict(
        conn.execute(
            """
            SELECT * FROM strategy_states
            WHERE resident_id = ? AND strategy_key = ? AND context_key = ?
            """,
            (experience["resident_id"], strategy, context_key),
        ).fetchone()
    )
    update_key = f"experience:{experience['id']}:strategy:{strategy}:{context_key}"
    conn.execute(
        """
        INSERT OR IGNORE INTO learning_updates
        (update_key, branch_key, tick_number, resident_id, experience_id,
         memory_id, target_type, target_key, before_json, after_json,
         update_reason, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, 'strategy', ?, ?, ?, ?, ?)
        """,
        (
            update_key,
            experience["branch_key"],
            experience["tick_number"],
            experience["resident_id"],
            experience["id"],
            memory["id"],
            f"{strategy}:{context_key}",
            _json(before),
            _json(after),
            "boundary_outcome_feedback",
            occurred_at,
        ),
    )
    return after


def _ingest_world_events(conn, tick_id, branch_key, tick_number):
    rows = conn.execute(
        """
        SELECT * FROM world_event_stream
        WHERE tick_id = ? AND resident_id IS NOT NULL
          AND event_type != 'observer_model_detail'
        ORDER BY id
        """,
        (tick_id,),
    ).fetchall()
    created = []
    for row in rows:
        payload = _load(row["payload"], {})
        summary = f"{row['title']}：{row['content']}"
        experience = _upsert_experience(
            conn,
            experience_key=f"world-event:{row['id']}:resident:{row['resident_id']}",
            branch_key=branch_key,
            tick_number=tick_number,
            resident_id=int(row["resident_id"]),
            source_type="world_event",
            source_id=row["id"],
            event_type=row["event_type"],
            summary=summary,
            outcome="success" if payload.get("success") is not False else "failure",
            location=row["location"],
            occurred_at=row["created_at"],
            evidence={"world_event_id": int(row["id"]), "payload": payload},
        )
        memory = _create_memory(
            conn, experience, row["event_type"], summary, payload, row["created_at"]
        )
        _learn_boundary_strategy(
            conn, experience, memory, payload, row["created_at"]
        )
        created.append(int(experience["id"]))
    return created


def _ingest_legacy_memories(conn, branch_key, tick_number, world_time, resident_ids):
    if not _table_exists(conn, "memories") or not resident_ids:
        return []
    placeholders = ", ".join("?" for _ in resident_ids)
    rows = conn.execute(
        f"""
        SELECT * FROM memories
        WHERE resident_id IN ({placeholders})
        ORDER BY id DESC LIMIT 120
        """,
        list(resident_ids),
    ).fetchall()
    created = []
    for row in rows:
        experience = _upsert_experience(
            conn,
            experience_key=f"legacy-memory:{row['id']}",
            branch_key=branch_key,
            tick_number=tick_number,
            resident_id=int(row["resident_id"]),
            source_type="legacy_memory",
            source_id=row["id"],
            event_type=f"legacy_{row['memory_type']}",
            summary=row["content"],
            outcome="remembered",
            location="",
            occurred_at=row["created_at"],
            evidence={"legacy_memory_id": int(row["id"]), "source": row["source"]},
        )
        memory = _create_memory(
            conn,
            experience,
            experience["event_type"],
            row["content"],
            {
                "confidence": min(1.0, 0.5 + int(row["importance"]) * 0.08),
            },
            row["created_at"],
        )
        if memory:
            created.append(int(memory["id"]))
    return created


def decay_adaptive_memories(conn, world_time=None):
    if world_time is None:
        now = datetime.now(WORLD_TZ)
    elif isinstance(world_time, datetime):
        now = world_time.astimezone(WORLD_TZ) if world_time.tzinfo else world_time.replace(tzinfo=WORLD_TZ)
    else:
        now = parse_world_datetime(world_time) or datetime.now(WORLD_TZ)
    rows = conn.execute(
        """
        SELECT * FROM adaptive_memories
        WHERE status IN ('active', 'weakened')
        """
    ).fetchall()
    weakened = 0
    forgotten = 0
    for row in rows:
        reinforced = parse_world_datetime(row["last_reinforced_at"])
        if not reinforced:
            continue
        age_days = max(0.0, (now - reinforced).total_seconds() / 86400)
        if age_days < 1:
            continue
        decay = min(0.3, age_days * (0.012 if row["memory_type"] == "semantic" else 0.025))
        strength = max(0.0, float(row["strength"]) - decay)
        status = "forgotten" if strength < 0.12 else ("weakened" if strength < 0.45 else "active")
        conn.execute(
            "UPDATE adaptive_memories SET strength = ?, status = ? WHERE id = ?",
            (round(strength, 4), status, row["id"]),
        )
        weakened += int(status == "weakened")
        forgotten += int(status == "forgotten")
    return {"weakened": weakened, "forgotten": forgotten}


def process_adaptive_learning(
    conn,
    *,
    world_time,
    tick_id,
    tick_number,
    branch_key,
    resident_ids,
):
    if not learning_runtime_available(conn):
        return {"available": False}
    experiences = _ingest_world_events(conn, tick_id, branch_key, tick_number)
    legacy = _ingest_legacy_memories(
        conn, branch_key, tick_number, world_time, resident_ids
    )
    decay = decay_adaptive_memories(conn, world_time)
    return {
        "available": True,
        "experiences_created": len(experiences),
        "legacy_memories_linked": len(legacy),
        "decay": decay,
    }


def get_adaptive_cognitive_context(conn, resident_id, branch_key="main", limit=8):
    if not learning_runtime_available(conn):
        return {
            "adaptive_memories": [],
            "strategy_states": [],
            "norm_beliefs": [],
        }
    memories = conn.execute(
        """
        SELECT memory.*, experience.event_type, experience.objective_summary,
               experience.location, experience.occurred_at, experience.branch_key
        FROM adaptive_memories memory
        JOIN experience_records experience ON experience.id = memory.experience_id
        WHERE memory.resident_id = ? AND experience.branch_key = ?
          AND memory.status IN ('active', 'weakened')
        ORDER BY (memory.salience * memory.confidence * memory.strength) DESC,
                 memory.id DESC LIMIT ?
        """,
        (resident_id, branch_key, limit),
    ).fetchall()
    strategies = conn.execute(
        """
        SELECT * FROM strategy_states
        WHERE resident_id = ? AND status = 'active'
        ORDER BY confidence DESC, last_updated_at DESC LIMIT ?
        """,
        (resident_id, limit),
    ).fetchall()
    norm_beliefs = []
    if _table_exists(conn, "agent_norm_beliefs"):
        norm_beliefs = conn.execute(
            """
            SELECT belief.*, norm.name, norm.behavior_key, norm.group_type,
                   norm.group_key, norm.context_type, norm.context_key,
                   norm.state
            FROM agent_norm_beliefs belief
            JOIN norm_candidates norm ON norm.id = belief.norm_id
            WHERE belief.resident_id = ? AND norm.state != 'dissolved'
            ORDER BY belief.confidence DESC, belief.last_updated_at DESC LIMIT ?
            """,
            (resident_id, limit),
        ).fetchall()
    return {
        "adaptive_memories": [dict(row) for row in memories],
        "strategy_states": [dict(row) for row in strategies],
        "norm_beliefs": [dict(row) for row in norm_beliefs],
    }
