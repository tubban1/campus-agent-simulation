from __future__ import annotations

import json
from app.json_utils import json_dumps
from datetime import datetime, timedelta, timezone
from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


SOCIAL_SIGNAL_TYPES = {
    "approval",
    "disapproval",
    "imitation",
    "reminder",
    "gossip",
    "exclusion",
    "sanction",
    "counterexample",
}


def _json(value):
    return json_dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _now(value=None):
    if value is None:
        return datetime.now(WORLD_TZ)
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    parsed = parse_world_datetime(value)
    if parsed:
        return parsed
    raise ValueError(f"无法解析的时间格式: {value}")


def _table_exists(conn, table_name):
    return bool(conn.execute(f"PRAGMA table_info({table_name})").fetchall())


def norm_runtime_available(conn):
    return _table_exists(conn, "norm_candidates")


def record_norm_signal(
    conn,
    *,
    signal_key,
    behavior_key,
    signal_type,
    stance,
    group_type,
    group_key,
    context_type,
    context_key,
    source_type,
    source_id,
    observed_at,
    resident_id=None,
    branch_key="main",
    tick_number=0,
    weight=1.0,
    details=None,
):
    conn.execute(
        """
        INSERT OR IGNORE INTO norm_signals
        (signal_key, branch_key, tick_number, resident_id, group_type,
         group_key, context_type, context_key, behavior_key, signal_type,
         stance, weight, source_type, source_id, observed_at, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_key,
            branch_key,
            tick_number,
            resident_id,
            group_type,
            group_key,
            context_type,
            context_key,
            behavior_key,
            signal_type,
            stance,
            float(weight),
            source_type,
            str(source_id),
            _now(observed_at).isoformat(),
            _json(details or {}),
        ),
    )
    return dict(
        conn.execute(
            "SELECT * FROM norm_signals WHERE signal_key = ?", (signal_key,)
        ).fetchone()
    )


def _capture_boundary_signals(conn, branch_key, tick_number, world_time):
    rows = conn.execute(
        """
        SELECT attempt.*, evaluation.target_key, evaluation.tick_number,
               resident.role
        FROM boundary_attempts attempt
        JOIN constraint_evaluations evaluation ON evaluation.id = attempt.evaluation_id
        JOIN residents resident ON resident.id = attempt.resident_id
        WHERE evaluation.tick_number <= ?
        ORDER BY attempt.id
        """,
        (tick_number,),
    ).fetchall()
    created = 0
    for row in rows:
        behavior = f"boundary:{row['strategy']}"
        group_key = row["role"] or "unknown"
        before = conn.execute(
            "SELECT id FROM norm_signals WHERE signal_key = ?",
            (f"boundary:{row['id']}:behavior",),
        ).fetchone()
        record_norm_signal(
            conn,
            signal_key=f"boundary:{row['id']}:behavior",
            behavior_key=behavior,
            signal_type="behavior",
            stance="neutral",
            group_type="role",
            group_key=group_key,
            context_type="space",
            context_key=row["target_key"],
            source_type="boundary_attempt",
            source_id=row["id"],
            observed_at=row["started_at"],
            resident_id=row["resident_id"],
            branch_key=branch_key,
            tick_number=int(row["tick_number"]),
            details={
                "succeeded": bool(row["succeeded"]),
                "detected": bool(row["detected"]),
                "harmed": bool(row["harmed"]),
            },
        )
        created += int(before is None)
        if row["detected"]:
            record_norm_signal(
                conn,
                signal_key=f"boundary:{row['id']}:disapproval",
                behavior_key=behavior,
                signal_type="sanction" if int(row["sanction_minor"]) else "disapproval",
                stance="oppose",
                group_type="role",
                group_key=group_key,
                context_type="space",
                context_key=row["target_key"],
                source_type="boundary_attempt",
                source_id=row["id"],
                observed_at=row["resolved_at"] or world_time,
                resident_id=row["resident_id"],
                branch_key=branch_key,
                tick_number=int(row["tick_number"]),
                weight=1.5 if int(row["sanction_minor"]) else 1,
            )
    return created


def _desired_state(current, behaviors, actors, feedback, support, oppose, confidence):
    if behaviors < 2 or actors < 2 or feedback < 1:
        return "dissolved" if current == "weakening" else "emerging"
    total = max(0.0001, support + oppose)
    support_ratio = support / total
    oppose_ratio = oppose / total
    if support >= 1 and oppose >= 1 and min(support_ratio, oppose_ratio) >= 0.25:
        return "contested"
    if behaviors >= 5 and actors >= 3 and support_ratio >= 0.65 and confidence >= 0.55:
        return "established"
    if current == "established" and (confidence < 0.4 or oppose_ratio > 0.55):
        return "weakening"
    return "emerging"


def _update_agent_beliefs(conn, norm_id, signals, descriptive, injunctive, now):
    resident_ids = {int(row["resident_id"]) for row in signals if row["resident_id"]}
    for resident_id in resident_ids:
        personal = [row for row in signals if row["resident_id"] == resident_id]
        support = sum(float(row["weight"]) for row in personal if row["stance"] == "support")
        oppose = sum(float(row["weight"]) for row in personal if row["stance"] == "oppose")
        stance = "support" if support > oppose else ("oppose" if oppose > support else "uncertain")
        exposure_count = len(personal)
        confidence = min(0.95, 0.25 + exposure_count * 0.12)
        conn.execute(
            """
            INSERT INTO agent_norm_beliefs
            (resident_id, norm_id, descriptive_expectation,
             injunctive_expectation, confidence, exposure_count,
             personal_stance, last_updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(resident_id, norm_id) DO UPDATE SET
                descriptive_expectation = excluded.descriptive_expectation,
                injunctive_expectation = excluded.injunctive_expectation,
                confidence = excluded.confidence,
                exposure_count = excluded.exposure_count,
                personal_stance = excluded.personal_stance,
                last_updated_at = excluded.last_updated_at,
                metadata_json = excluded.metadata_json
            """,
            (
                resident_id,
                norm_id,
                descriptive,
                injunctive,
                confidence,
                exposure_count,
                stance,
                now.isoformat(),
                _json({"signal_ids": [int(row["id"]) for row in personal]}),
            ),
        )


def detect_norms(conn, world_time=None, window_days=14):
    if not norm_runtime_available(conn):
        return {"available": False}
    now = _now(world_time)
    start = now - timedelta(days=window_days)
    rows = conn.execute(
        """
        SELECT * FROM norm_signals
        WHERE observed_at >= ?
        ORDER BY id
        """,
        (start.isoformat(),),
    ).fetchall()
    grouped = {}
    for row in rows:
        key = (
            row["group_type"],
            row["group_key"],
            row["context_type"],
            row["context_key"],
            row["behavior_key"],
        )
        grouped.setdefault(key, []).append(dict(row))
    updated = []
    for key, signals in grouped.items():
        behavior_signals = [row for row in signals if row["signal_type"] == "behavior"]
        feedback_signals = [row for row in signals if row["signal_type"] in SOCIAL_SIGNAL_TYPES]
        actors = {row["resident_id"] for row in behavior_signals if row["resident_id"]}
        if len(behavior_signals) < 2 or len(actors) < 2 or not feedback_signals:
            continue
        group_type, group_key, context_type, context_key, behavior_key = key
        norm_key = ":".join(key)
        existing = conn.execute(
            "SELECT * FROM norm_candidates WHERE norm_key = ?", (norm_key,)
        ).fetchone()
        current = existing["state"] if existing else "none"
        support = sum(float(row["weight"]) for row in feedback_signals if row["stance"] == "support")
        oppose = sum(float(row["weight"]) for row in feedback_signals if row["stance"] == "oppose")
        descriptive = min(1.0, len(actors) / max(3, len(actors) + 1))
        injunctive = min(1.0, (support + oppose) / max(3, len(signals)))
        coverage = min(1.0, len({row["resident_id"] for row in signals if row["resident_id"]}) / max(3, len(actors)))
        confidence = min(
            0.98,
            0.2
            + min(0.35, len(behavior_signals) * 0.05)
            + min(0.3, len(feedback_signals) * 0.08)
            + coverage * 0.15,
        )
        desired = _desired_state(
            current,
            len(behavior_signals),
            len(actors),
            len(feedback_signals),
            support,
            oppose,
            confidence,
        )
        conn.execute(
            """
            INSERT INTO norm_candidates
            (norm_key, name, behavior_key, group_type, group_key,
             context_type, context_key, state, support_score,
             opposition_score, descriptive_expectation,
             injunctive_expectation, observation_coverage, behavior_count,
             distinct_actor_count, feedback_count, confidence,
             evidence_window_start, evidence_window_end, first_detected_at,
             last_updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(norm_key) DO UPDATE SET
                state = excluded.state,
                support_score = excluded.support_score,
                opposition_score = excluded.opposition_score,
                descriptive_expectation = excluded.descriptive_expectation,
                injunctive_expectation = excluded.injunctive_expectation,
                observation_coverage = excluded.observation_coverage,
                behavior_count = excluded.behavior_count,
                distinct_actor_count = excluded.distinct_actor_count,
                feedback_count = excluded.feedback_count,
                confidence = excluded.confidence,
                evidence_window_start = excluded.evidence_window_start,
                evidence_window_end = excluded.evidence_window_end,
                last_updated_at = excluded.last_updated_at,
                version = norm_candidates.version + 1,
                metadata_json = excluded.metadata_json
            """,
            (
                norm_key,
                f"{group_key}在{context_key}对{behavior_key}的共同预期",
                behavior_key,
                group_type,
                group_key,
                context_type,
                context_key,
                desired,
                support,
                oppose,
                descriptive,
                injunctive,
                coverage,
                len(behavior_signals),
                len(actors),
                len(feedback_signals),
                confidence,
                start.isoformat(),
                now.isoformat(),
                existing["first_detected_at"] if existing else now.isoformat(),
                now.isoformat(),
                _json({"signal_count": len(signals)}),
            ),
        )
        norm = conn.execute(
            "SELECT * FROM norm_candidates WHERE norm_key = ?", (norm_key,)
        ).fetchone()
        for signal in signals:
            conn.execute(
                """
                INSERT OR IGNORE INTO norm_evidence
                (norm_id, signal_id, evidence_type, resident_id, stance,
                 weight, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    norm["id"],
                    signal["id"],
                    signal["signal_type"],
                    signal["resident_id"],
                    signal["stance"],
                    signal["weight"],
                    signal["observed_at"],
                ),
            )
        if current != desired:
            conn.execute(
                """
                INSERT INTO norm_state_transitions
                (norm_id, from_state, to_state, trigger_type,
                 evidence_summary_json, transitioned_at)
                VALUES (?, ?, ?, 'evidence_window', ?, ?)
                """,
                (
                    norm["id"],
                    current,
                    desired,
                    _json(
                        {
                            "behaviors": len(behavior_signals),
                            "actors": len(actors),
                            "feedback": len(feedback_signals),
                            "support": support,
                            "oppose": oppose,
                        }
                    ),
                    now.isoformat(),
                ),
            )
        _update_agent_beliefs(
            conn, int(norm["id"]), signals, descriptive, injunctive, now
        )
        updated.append(int(norm["id"]))
    return {"available": True, "updated_norm_ids": updated, "signal_count": len(rows)}


def process_norm_emergence(conn, *, branch_key, tick_number, world_time):
    if not norm_runtime_available(conn):
        return {"available": False}
    created = _capture_boundary_signals(
        conn, branch_key, tick_number, world_time
    )
    detected = detect_norms(conn, world_time)
    return {
        "available": True,
        "signals_created": created,
        **detected,
    }
