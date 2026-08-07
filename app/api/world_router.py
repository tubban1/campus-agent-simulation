from fastapi import APIRouter, Request


router = APIRouter(tags=["world"])


@router.get("/api/world/observer-state")
def get_world_observer_state(request: Request):
    return request.app.state.get_world_observer_state()


@router.get("/api/world/snapshots")
def list_world_snapshots(request: Request, limit: int = 30):
    return request.app.state.list_world_snapshots(limit)


@router.get("/api/world/update-schedules")
def list_world_update_schedules(request: Request):
    return request.app.state.list_world_update_schedules()


@router.get("/api/world/update-runs")
def list_world_update_runs(
    request: Request,
    update_key: str = "",
    status: str = "",
    limit: int = 50,
):
    return request.app.state.list_world_update_runs(update_key, status, limit)


@router.get("/api/world/snapshots/{snapshot_id}")
def get_world_snapshot(snapshot_id: int, request: Request, include_state: bool = False):
    return request.app.state.get_world_snapshot(snapshot_id, include_state)
