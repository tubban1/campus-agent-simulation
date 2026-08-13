from fastapi import APIRouter, Request
from app.social_models import ChatRequest, NegotiateRequest, CollaborateRequest, CompeteRequest, LongTermGoalRequest, GroupGoalRequest


router = APIRouter(tags=["social"])


@router.post("/api/goals")
def create_goal(payload: LongTermGoalRequest, request: Request):
    return request.app.state.create_long_term_goal(payload)

@router.post("/api/groups")
def create_group(payload: GroupGoalRequest, request: Request):
    return request.app.state.create_group_goal(payload)

@router.post("/api/social/communicate")
def communicate(payload: ChatRequest, request: Request):
    return request.app.state.social_communicate(payload)

@router.post("/api/social/negotiate")
def negotiate(payload: NegotiateRequest, request: Request):
    return request.app.state.social_negotiate(payload)

@router.post("/api/social/collaborate")
def collaborate(payload: CollaborateRequest, request: Request):
    return request.app.state.social_collaborate(payload)

@router.post("/api/social/compete")
def compete(payload: CompeteRequest, request: Request):
    return request.app.state.social_compete(payload)

@router.get("/api/social/hierarchy")
def social_hierarchy(request: Request):
    return request.app.state.get_social_hierarchy()


@router.get("/api/social/relationships/{resident_id}")
def social_relationships(resident_id: int, request: Request):
    return request.app.state.get_social_relationships(resident_id)


@router.get("/api/organizations")
def organizations(request: Request):
    return request.app.state.get_organizations()


@router.get("/api/groups")
def groups(request: Request):
    return request.app.state.get_groups()
