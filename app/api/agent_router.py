from fastapi import APIRouter, HTTPException, Request
from app.social_models import ChatRequest
from app.tools_models import MoveRequest, BuySellRequest


router = APIRouter(tags=["agents"])


@router.post("/api/tools/move")
def move(payload: MoveRequest, request: Request):
    return request.app.state.tool_move(payload)

@router.post("/api/tools/chat")
def chat(payload: ChatRequest, request: Request):
    return request.app.state.tool_chat(payload)

@router.post("/api/tools/buy-sell")
def buy_sell(payload: BuySellRequest, request: Request):
    return request.app.state.tool_buy_sell(payload)

@router.post("/api/agent/decide/{resident_id}")
def decide(resident_id: int, request: Request):
    return request.app.state.decide_agent(resident_id)


@router.post("/api/agent/act/{resident_id}")
def act(resident_id: int, request: Request):
    return request.app.state.act_agent(resident_id)


@router.post("/api/agent/act-all")
def act_all(request: Request):
    return request.app.state.act_all_agents()


@router.get("/api/agents/{resident_id}/learning")
def learning(resident_id: int, request: Request):
    return request.app.state.get_agent_learning(resident_id)


@router.get("/api/agents/{resident_id}/long-term-goals")
def long_term_goals(resident_id: int, request: Request):
    return request.app.state.get_long_term_goals(resident_id)


@router.get("/api/agents/{resident_id}/goal-system")
def goal_system(resident_id: int, request: Request):
    return request.app.state.get_agent_goal_system(resident_id)


@router.get("/api/agents")
def get_agents(request: Request):
    return request.app.state.list_agents()


@router.get("/api/residents")
def get_residents(request: Request):
    return request.app.state.list_agents()


@router.get("/api/agents/modules")
def get_agents_modules(request: Request):
    return request.app.state.list_agent_modules()


@router.get("/api/agents/{resident_id}/modules")
def get_agent_modules(resident_id: int, request: Request):
    state = request.app.state.get_agent_modules(resident_id)
    if not state:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return state


@router.get("/api/agents/{resident_id}/memories/relevant")
def get_relevant_agent_memories(resident_id: int, request: Request, query: str = ""):
    return request.app.state.get_relevant_agent_memories(resident_id, query)


@router.get("/api/agents/{resident_id}/memories")
def get_agent_memories(
    resident_id: int,
    request: Request,
    limit: int = 20,
    offset: int = 0,
):
    return request.app.state.get_agent_memories(resident_id, limit, offset)


@router.get("/api/agents/{resident_id}/social-graph")
def get_social_graph(resident_id: int, request: Request, limit: int = 10):
    return request.app.state.get_social_graph(resident_id, limit)


@router.get("/api/agents/{resident_id}/timeline")
def get_timeline(resident_id: int, request: Request, limit: int = 30, offset: int = 0):
    return request.app.state.get_timeline(resident_id, limit, offset)


@router.get("/api/agents/{resident_id}/simulation-logs")
def get_simulation_logs(resident_id: int, request: Request, limit: int = 12):
    return request.app.state.get_simulation_logs(resident_id, limit)


@router.get("/api/agents/{resident_id}/profile-activity")
def get_profile_activity(resident_id: int, request: Request, timeline_limit: int = 20):
    return request.app.state.get_profile_activity(resident_id, timeline_limit)
