from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel


router = APIRouter(tags=["lifecycle"])


class ObserverSessionRequest(BaseModel):
    session_id: Optional[int] = None
    user_id: str = "anonymous"
    session_type: str = "observer"
    focused_resident_id: Optional[int] = None
    focused_location: str = ""


@router.post("/api/world/observer-sessions")
def observer_session(payload: ObserverSessionRequest, request: Request):
    return request.app.state.upsert_observer_session(payload)


@router.post("/api/simulate/lifecycle-step/{resident_id}")
def lifecycle_step(resident_id: int, request: Request):
    return request.app.state.simulate_lifecycle_step(resident_id)


@router.post("/api/simulate/lifecycle-round")
def lifecycle_round(request: Request):
    return request.app.state.simulate_lifecycle_round()


@router.post("/api/simulate/ai-day")
def ai_day(request: Request):
    return request.app.state.simulate_ai_day()


@router.get("/api/agents/{resident_id}/life-course/overview")
def life_course_overview(resident_id: int, request: Request, from_day: Optional[int] = None, to_day: Optional[int] = None, limit: int = 240):
    return request.app.state.life_course_overview(resident_id, from_day, to_day, limit)


@router.get("/api/agents/{resident_id}/life-course/events")
def life_course_events(resident_id: int, request: Request, from_day: Optional[int] = None, to_day: Optional[int] = None, limit: int = 240):
    return request.app.state.life_course_events(resident_id, from_day, to_day, limit)


@router.get("/api/agents/{resident_id}/life-course/turning-points")
def life_course_turning_points(resident_id: int, request: Request, limit: int = 12):
    return request.app.state.life_course_turning_points(resident_id, limit)


@router.get("/api/agents/{resident_id}/life-course/relationships")
def life_course_relationships(resident_id: int, request: Request):
    return request.app.state.life_course_relationships(resident_id)


@router.get("/api/agents/{resident_id}/life-course/groups")
def life_course_groups(resident_id: int, request: Request):
    return request.app.state.life_course_groups(resident_id)
