from fastapi import APIRouter, HTTPException, Request


router = APIRouter(tags=["agents"])


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
