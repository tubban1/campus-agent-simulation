"""R2.1 Subject Continuity & Cognitive Gap Detection.

Allows agents to build routine expectations, detect missing timeline evidence or
unexpected gaps, and form competing hypotheses without global DB truth leakage.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def ensure_roadmap2_tables_exist(conn) -> bool:
    try:
        tables = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(group_pattern_candidates)").fetchall()
        }
        return bool(tables)
    except Exception:
        return False


def update_agent_expectations(
    conn, resident_id: int, day: int, branch_key: str = "main"
) -> List[Dict[str, Any]]:
    """Build or refine routine expectations based on agent spatial memories and commitments."""
    # Query frequent locations from spatial memories
    mem_rows = conn.execute(
        """
        SELECT node.name AS location, COUNT(*) as visit_freq
        FROM agent_spatial_memories memory
        JOIN spatial_nodes node ON node.id = memory.node_id
        WHERE memory.resident_id = ? AND memory.branch_key = ?
        GROUP BY node.name
        ORDER BY visit_freq DESC
        LIMIT 3
        """,
        (resident_id, branch_key),
    ).fetchall()

    created_expectations = []
    for row in mem_rows:
        loc = str(row["location"])
        freq = int(row["visit_freq"])
        confidence = min(95, 40 + freq * 15)

        pattern = {
            "expected_location": loc,
            "min_visits_per_day": max(1, freq // 2),
            "source": "memory_frequency",
        }
        
        # Check existing expectation
        existing = conn.execute(
            """
            SELECT id FROM agent_expectations
            WHERE resident_id = ? AND expectation_type = 'routine_location'
              AND subject_key = ? AND branch_key = ?
            """,
            (resident_id, loc, branch_key),
        ).fetchone()

        if not existing:
            cursor = conn.execute(
                """
                INSERT INTO agent_expectations
                (resident_id, expectation_type, subject_key, expected_pattern_json,
                 confidence, branch_key, created_day)
                VALUES (?, 'routine_location', ?, ?, ?, ?, ?)
                """,
                (resident_id, loc, json.dumps(pattern), confidence, branch_key, day),
            )
            created_expectations.append({
                "resident_id": resident_id,
                "expectation_type": "routine_location",
                "subject_key": loc,
                "confidence": confidence,
            })
    return created_expectations


def detect_continuity_gaps(
    conn, resident_id: int, day: int, current_tick_id: Optional[int] = None, branch_key: str = "main"
) -> List[Dict[str, Any]]:
    """Detect unobserved intervals or missing expected events for an agent."""
    expectations = conn.execute(
        """
        SELECT * FROM agent_expectations
        WHERE resident_id = ? AND branch_key = ?
        """,
        (resident_id, branch_key),
    ).fetchall()

    if not expectations:
        return []

    # Get today's observations for the resident
    obs_count = conn.execute(
        """
        SELECT COUNT(*) as count FROM agent_observations
        WHERE observer_resident_id = ? AND branch_key = ?
        """,
        (resident_id, branch_key),
    ).fetchone()["count"]

    gaps = []
    # If agent has routine expectations but zero recent observations today, flag a gap
    if obs_count == 0 and day > 1:
        gap_summary = f"第 {day} 天没有记录到任何局部环境感知，存在主体感知断层。"
        existing_gap = conn.execute(
            """
            SELECT id FROM continuity_observations
            WHERE resident_id = ? AND gap_type = 'unobserved_interval'
              AND time_window_start = ? AND branch_key = ?
            """,
            (resident_id, f"Day {day}", branch_key),
        ).fetchone()

        if not existing_gap:
            conn.execute(
                """
                INSERT INTO continuity_observations
                (resident_id, gap_type, time_window_start, time_window_end,
                 gap_magnitude, summary, status, branch_key)
                VALUES (?, 'unobserved_interval', ?, ?, 0.8, ?, 'unexplained', ?)
                """,
                (resident_id, f"Day {day}", f"Day {day}", gap_summary, branch_key),
            )
            gaps.append({
                "resident_id": resident_id,
                "gap_type": "unobserved_interval",
                "summary": gap_summary,
                "magnitude": 0.8,
            })

    return gaps


def generate_agent_hypotheses(
    conn, resident_id: int, continuity_observation_id: int, branch_key: str = "main"
) -> List[Dict[str, Any]]:
    """Form competing hypotheses for a cognitive gap."""
    gap = conn.execute(
        """
        SELECT * FROM continuity_observations WHERE id = ?
        """,
        (continuity_observation_id,),
    ).fetchone()

    if not gap:
        return []

    hypotheses_templates = [
        ("当时陷入深度睡眠或休息状态，未关注外界环境", 0.6, ["internal_state"]),
        ("相关场所暂时关闭或路线受阻，导致未能按计划前往", 0.5, ["environmental_barrier"]),
        ("发生了未被察觉的临时突发事件，扰乱了日常活动", 0.4, ["external_event"]),
    ]

    results = []
    for text, likelihood, evidence in hypotheses_templates:
        existing = conn.execute(
            """
            SELECT id FROM agent_hypotheses
            WHERE resident_id = ? AND continuity_observation_id = ?
              AND hypothesis_text = ?
            """,
            (resident_id, continuity_observation_id, text),
        ).fetchone()

        if not existing:
            conn.execute(
                """
                INSERT INTO agent_hypotheses
                (resident_id, continuity_observation_id, hypothesis_text,
                 likelihood_score, evidence_json, status, branch_key)
                VALUES (?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    resident_id,
                    continuity_observation_id,
                    text,
                    likelihood,
                    json.dumps(evidence),
                    branch_key,
                ),
            )
            results.append({
                "hypothesis_text": text,
                "likelihood_score": likelihood,
            })

    # Update gap status to investigating
    conn.execute(
        """
        UPDATE continuity_observations
        SET status = 'investigating'
        WHERE id = ?
        """,
        (continuity_observation_id,),
    )

    return results
