"""Repair critically degraded resident body states without deleting history.

This is an explicit, one-off operational repair after the former health-decay
model exhausted the initial population. It records a single audit event and
only changes residents who have crossed a critical physiological threshold.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.body_runtime import _summary_energy
from app.db import get_connection
from app.world_runtime.clock import get_world_now
# app.main binds runtime_schema's pluggable dependencies during application
# initialization; use that fully configured entry point for the audit event.
from app.main import append_world_event


def stabilize(*, apply: bool) -> int:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT resident_id, hunger, fatigue, sleep_debt, stress, attention,
                   social_energy, health, weather_exposure
            FROM agent_body_states
            WHERE health < 35 OR hunger > 95 OR fatigue > 92
            ORDER BY resident_id
            """
        ).fetchall()
        if not rows:
            print("No critically degraded body states found.")
            return 0
        print(f"Critical body states found: {len(rows)}")
        if not apply:
            print("Dry run only. Re-run with --apply to write the recovery.")
            return len(rows)

        now = get_world_now().isoformat()
        repaired_ids = []
        for raw in rows:
            state = dict(raw)
            repaired = {
                "hunger": min(float(state["hunger"]), 55.0),
                "fatigue": min(float(state["fatigue"]), 55.0),
                "sleep_debt": min(float(state["sleep_debt"]), 30.0),
                "stress": min(float(state["stress"]), 40.0),
                "attention": max(float(state["attention"]), 55.0),
                "social_energy": max(float(state["social_energy"]), 45.0),
                "health": max(float(state["health"]), 75.0),
                "weather_exposure": min(float(state["weather_exposure"]), 10.0),
            }
            energy = _summary_energy(repaired)
            conn.execute(
                """
                UPDATE agent_body_states
                SET hunger = ?, fatigue = ?, sleep_debt = ?, stress = ?,
                    attention = ?, social_energy = ?, health = ?,
                    weather_exposure = ?, last_updated_at = ?,
                    version = version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE resident_id = ?
                """,
                (
                    repaired["hunger"], repaired["fatigue"], repaired["sleep_debt"],
                    repaired["stress"], repaired["attention"], repaired["social_energy"],
                    repaired["health"], repaired["weather_exposure"], now,
                    state["resident_id"],
                ),
            )
            conn.execute(
                "UPDATE agent_profiles SET energy = ? WHERE resident_id = ?",
                (energy, state["resident_id"]),
            )
            repaired_ids.append(int(state["resident_id"]))
        append_world_event(
            conn,
            "body_state_stabilized",
            "居民身体状态已临床稳定化",
            f"已修复 {len(repaired_ids)} 名因旧健康衰减模型而进入濒危状态的居民。",
            payload={"resident_ids": repaired_ids, "reason": "health_decay_model_repair"},
            source_type="operational_repair",
            rule_version="body-health-v2",
        )
        conn.commit()
        print(f"Stabilized {len(repaired_ids)} residents: {repaired_ids}")
        return len(repaired_ids)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write the audited recovery to the configured database.")
    args = parser.parse_args()
    stabilize(apply=args.apply)


if __name__ == "__main__":
    main()
