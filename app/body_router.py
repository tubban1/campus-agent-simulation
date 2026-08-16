from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.body_runtime import body_runtime_available, _summary_energy
from app.body_schemas import AgentBodyStateResponse, AgentBodyStatesResponse
from app.db import get_connection


router = APIRouter(tags=["body"])


def _alerts(state):
    state = dict(state)
    alerts = []
    if float(state["fatigue"]) >= 75:
        alerts.append("疲劳")
    if float(state["hunger"]) >= 80:
        alerts.append("饥饿")
    if float(state.get("hydration") or 0) >= 70:
        alerts.append("缺水")
    if float(state.get("nutrition") or 100) <= 35:
        alerts.append("营养不足")
    if float(state["stress"]) >= 75:
        alerts.append("高压力")
    if float(state["attention"]) <= 25:
        alerts.append("注意力不足")
    if float(state["health"]) <= 55:
        alerts.append("健康风险")
    if float(state.get("illness_load") or 0) >= 50:
        alerts.append("身体不适")
    return alerts


def _body_rows(conn, resident_id=None):
    where = "WHERE body.resident_id = ?" if resident_id is not None else ""
    params = (resident_id,) if resident_id is not None else ()
    rows = conn.execute(
        f"""
        SELECT body.*, residents.name AS resident_name, residents.role,
               residents.location
        FROM agent_body_states body
        JOIN residents ON residents.id = body.resident_id
        {where}
        ORDER BY body.resident_id
        """,
        params,
    ).fetchall()
    return [
        {
            **dict(row),
            # Old databases can still serve the core body state before the
            # daily-needs migration is applied.  Keep the read API useful
            # during that rollout rather than returning a schema error.
            "hydration": dict(row).get("hydration", 25.0),
            "nutrition": dict(row).get("nutrition", 78.0),
            "activity_load": dict(row).get("activity_load", 18.0),
            "illness_load": dict(row).get("illness_load", 0.0),
            # Energy is a derived presentation value. Body state remains the
            # single source of truth, but the profile UI needs this number to
            # avoid falling back to a stale agent_profiles value.
            "energy": _summary_energy(dict(row)),
            "alerts": _alerts(row),
        }
        for row in rows
    ]


@router.get("/api/body-states", response_model=AgentBodyStatesResponse)
def list_body_states():
    with get_connection() as conn:
        if not body_runtime_available(conn):
            return {"agents": []}
        return {"agents": _body_rows(conn)}


@router.get(
    "/api/agents/{resident_id}/body-state",
    response_model=AgentBodyStateResponse,
)
def get_agent_body_state(resident_id: int):
    with get_connection() as conn:
        if not body_runtime_available(conn):
            raise HTTPException(status_code=409, detail="身体状态运行时尚未初始化")
        rows = _body_rows(conn, resident_id)
        if not rows:
            raise HTTPException(status_code=404, detail="Agent 身体状态不存在")
        return rows[0]


@router.post("/api/body-states/reset")
def reset_all_body_states():
    with get_connection() as conn:
        if not body_runtime_available(conn):
            raise HTTPException(status_code=409, detail="身体状态运行时尚未初始化")
        conn.execute(
            """
            UPDATE agent_body_states
            SET hunger = 0.0, fatigue = 0.0, hydration = 0.0, nutrition = 90.0,
                stress = 0.0, attention = 100.0, health = 100.0, sleep_debt = 0.0,
                weather_exposure = 0.0, activity_load = 0.0, illness_load = 0.0
            """
        )
        conn.execute("UPDATE agent_profiles SET energy = 100")
        conn.commit()
        return {"status": "ok", "message": "所有 Agent 身体生理状态已成功重置恢复"}
