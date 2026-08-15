"""R2.2 Group Pattern Observer.

Detects statistical spatiotemporal clusters, information diffusion chains,
and anomalous crowd aggregations from atomic events without hardcoding narratives.
"""

from __future__ import annotations

from collections import defaultdict
import json
from typing import Any, Dict, List


def scan_group_pattern_candidates(
    conn, day: int, branch_key: str = "main"
) -> List[Dict[str, Any]]:
    """Scan recent events to detect group pattern candidates."""
    # Group recent events by location and time window / slot
    events = conn.execute(
        """
        SELECT id, day, slot, location, event_type, resident_id, title, content
        FROM world_event_stream
        WHERE day = ? AND branch_key = ? AND location != ''
        ORDER BY id DESC
        LIMIT 100
        """,
        (day, branch_key),
    ).fetchall()

    if not events:
        return []

    location_clusters = defaultdict(list)
    for ev in events:
        loc = str(ev["location"])
        location_clusters[loc].append(dict(ev))

    candidates_created = []
    for location, ev_list in location_clusters.items():
        # Count unique participants
        participants = {
            int(ev["resident_id"]) for ev in ev_list if ev.get("resident_id") is not None
        }
        event_ids = [int(ev["id"]) for ev in ev_list]
        participant_count = len(participants)

        if participant_count >= 2 or len(ev_list) >= 4:
            # Determine pattern type
            event_types = {ev["event_type"] for ev in ev_list}
            if any("chat" in et or "social" in et for et in event_types):
                pattern_type = "social_gathering"
                title = f"{location} 社交互动聚集模式"
            elif any("canteen" in location or "food" in et for et in event_types):
                pattern_type = "canteen_peak"
                title = f"{location} 用餐高峰流向"
            else:
                pattern_type = "spatial_cluster"
                title = f"{location} 人员聚集活动"

            density_ratio = round(participant_count / 10.0 + len(ev_list) * 0.1, 2)
            baseline_deviation = round(density_ratio * 0.4, 2)
            candidate_score = round(min(1.0, 0.3 + participant_count * 0.2 + len(ev_list) * 0.05), 2)

            existing = conn.execute(
                """
                SELECT id, participant_count FROM group_pattern_candidates
                WHERE location = ? AND pattern_type = ? AND status IN ('candidate', 'confirmed')
                  AND branch_key = ?
                """,
                (location, pattern_type, branch_key),
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE group_pattern_candidates
                    SET participant_count = ?, density_ratio = ?, baseline_deviation = ?,
                        candidate_score = ?, evidence_event_ids_json = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        participant_count,
                        density_ratio,
                        baseline_deviation,
                        candidate_score,
                        json.dumps(event_ids),
                        existing["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO group_pattern_candidates
                    (pattern_type, title, location, time_window_start, time_window_end,
                     participant_count, density_ratio, baseline_deviation, candidate_score,
                     status, evidence_event_ids_json, branch_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?)
                    """,
                    (
                        pattern_type,
                        title,
                        location,
                        f"Day {day}",
                        f"Day {day}",
                        participant_count,
                        density_ratio,
                        baseline_deviation,
                        candidate_score,
                        json.dumps(event_ids),
                        branch_key,
                    ),
                )
                candidates_created.append({
                    "title": title,
                    "location": location,
                    "pattern_type": pattern_type,
                    "participant_count": participant_count,
                    "candidate_score": candidate_score,
                })

    return candidates_created


def promote_or_expire_patterns(conn, day: int, branch_key: str = "main") -> Dict[str, int]:
    """Promote strong pattern candidates to confirmed, expire old ones."""
    # Promote candidates with score >= 0.6 and participants >= 2
    promoted = conn.execute(
        """
        UPDATE group_pattern_candidates
        SET status = 'confirmed', updated_at = CURRENT_TIMESTAMP
        WHERE status = 'candidate' AND candidate_score >= 0.6 AND participant_count >= 2
          AND branch_key = ?
        """,
        (branch_key,),
    ).rowcount

    # Expire outdated patterns
    expired = conn.execute(
        """
        UPDATE group_pattern_candidates
        SET status = 'expired', updated_at = CURRENT_TIMESTAMP
        WHERE status IN ('candidate', 'confirmed') AND time_window_start < ?
          AND branch_key = ?
        """,
        (f"Day {max(1, day - 2)}", branch_key),
    ).rowcount

    return {"promoted": promoted, "expired": expired}


def get_active_group_patterns(
    conn, branch_key: str = "main", limit: int = 10
) -> List[Dict[str, Any]]:
    """Query active or confirmed group pattern candidates."""
    rows = conn.execute(
        """
        SELECT * FROM group_pattern_candidates
        WHERE status IN ('candidate', 'confirmed') AND branch_key = ?
        ORDER BY candidate_score DESC, id DESC
        LIMIT ?
        """,
        (branch_key, limit),
    ).fetchall()

    return [dict(row) for row in rows]
