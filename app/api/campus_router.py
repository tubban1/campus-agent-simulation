from fastapi import APIRouter, Request

from app.campus_models import CampusEnvironmentRequest, CampusEventRequest, SpaceStatusRequest
from app.policy_models import PolicyRequest, VotePolicyRequest


router = APIRouter(tags=["campus"])


@router.post("/api/campus/environment/sync-real-time")
def sync_real_time(request: Request):
    return request.app.state.sync_real_time()


@router.post("/api/campus/environment/sync-real-weather")
def sync_real_weather(request: Request):
    return request.app.state.sync_real_weather()


@router.post("/api/campus/environment/set")
def set_environment(payload: CampusEnvironmentRequest, request: Request):
    return request.app.state.set_today_environment(payload)


@router.post("/api/campus/spaces/{location}/status")
def set_space_status(location: str, payload: SpaceStatusRequest, request: Request):
    return request.app.state.set_space_status(location, payload)


@router.post("/api/campus/events/trigger")
def trigger_event(payload: CampusEventRequest, request: Request):
    return request.app.state.trigger_campus_event(payload)


@router.post("/api/campus/events/{event_id}/resolve")
def resolve_event(event_id: int, request: Request):
    return request.app.state.resolve_campus_event(event_id)


@router.get("/api/policies")
def policies(request: Request):
    return request.app.state.get_policies()

@router.post("/api/tools/submit-policy")
def submit_policy(payload: PolicyRequest, request: Request):
    return request.app.state.submit_policy(payload)

@router.post("/api/tools/vote-policy")
def vote_policy(payload: VotePolicyRequest, request: Request):
    return request.app.state.vote_policy(payload)

@router.post("/api/tools/close-policy/{policy_id}")
def close_policy(policy_id: int, request: Request):
    return request.app.state.close_policy(policy_id)

@router.post("/api/tools/daily-reflect")
def daily_reflect(request: Request):
    return request.app.state.daily_reflect()


@router.get("/api/inventory")
def get_inventory(request: Request):
    return request.app.state.get_inventory()


@router.get("/api/campus/environment/today")
def get_today_environment(request: Request):
    return request.app.state.get_today_environment()


@router.get("/api/campus/spaces")
def get_campus_spaces(request: Request):
    return request.app.state.get_campus_spaces()
