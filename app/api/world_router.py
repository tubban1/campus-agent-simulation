from typing import Optional

from fastapi import APIRouter, Header, Request

from app.world_state.models import (
    EnvironmentConfigRequest,
    WorldBranchRequest,
    WorldBranchSwitchRequest,
    WorldSnapshotRequest,
    WorldSnapshotRestoreRequest,
)
from app.admin_models import AdminWorldEventRequest


router = APIRouter(tags=["world"])


@router.post("/api/admin/world/start")
def start_world(request: Request, authorization: str = Header(default=None)):
    return request.app.state.start_world_runtime(authorization)


@router.post("/api/admin/world/pause")
def pause_world(request: Request, authorization: str = Header(default=None)):
    return request.app.state.pause_world_runtime(authorization)


@router.post("/api/admin/world/tick")
def tick_world(request: Request, authorization: str = Header(default=None)):
    return request.app.state.run_world_tick_once(authorization)


@router.post("/api/admin/events/trigger")
def trigger_admin_event(payload: AdminWorldEventRequest, request: Request, authorization: str = Header(default=None)):
    return request.app.state.trigger_admin_world_event(payload, authorization)


@router.post("/api/admin/world/environment-configs")
def create_environment_config(payload: EnvironmentConfigRequest, request: Request, authorization: str = Header(default=None)):
    return request.app.state.create_environment_config(payload, authorization)


@router.post("/api/admin/world/environment-configs/{config_id}/activate")
def activate_environment_config(config_id: int, request: Request, authorization: str = Header(default=None)):
    return request.app.state.activate_environment_config(config_id, authorization)


@router.post("/api/admin/world/snapshots")
def create_snapshot(payload: WorldSnapshotRequest, request: Request, authorization: str = Header(default=None)):
    return request.app.state.create_world_snapshot(payload, authorization)


@router.post("/api/admin/world/snapshots/{snapshot_id}/restore")
def restore_snapshot(snapshot_id: int, payload: WorldSnapshotRestoreRequest, request: Request, authorization: str = Header(default=None)):
    return request.app.state.restore_world_snapshot(snapshot_id, payload, authorization)


@router.post("/api/admin/world/branches")
def create_branch(payload: WorldBranchRequest, request: Request, authorization: str = Header(default=None)):
    return request.app.state.create_world_branch(payload, authorization)


@router.post("/api/admin/world/branches/{branch_key}/switch")
def switch_branch(branch_key: str, payload: WorldBranchSwitchRequest, request: Request, authorization: str = Header(default=None)):
    return request.app.state.switch_world_branch(branch_key, payload, authorization)


@router.get("/api/state")
def state(request: Request):
    return request.app.state.get_state()


@router.get("/api/world/observer-state")
def get_world_observer_state(request: Request):
    return request.app.state.get_world_observer_state()


@router.get("/api/world/snapshots")
def list_world_snapshots(request: Request, limit: int = 30):
    return request.app.state.list_world_snapshots(limit)


@router.get("/api/world/branches")
def list_world_branches(request: Request):
    return request.app.state.list_world_branches()


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


@router.get("/api/world/runtime")
def runtime(request: Request):
    return request.app.state.get_world_runtime()


@router.get("/api/world/environment-config")
def environment_config(request: Request):
    return request.app.state.get_environment_config()


@router.get("/api/world/environment-configs")
def environment_configs(request: Request, limit: int = 50):
    return request.app.state.list_environment_configs(limit)


@router.get("/api/world/action-rules")
def action_rules(request: Request):
    return request.app.state.list_action_rules()


@router.get("/api/world/action-executions")
def action_executions(request: Request, resident_id: Optional[int] = None, status: str = "", limit: int = 50):
    return request.app.state.list_action_executions(resident_id, status, limit)


@router.get("/api/world/delayed-effects")
def delayed_effects(request: Request, status: str = "", limit: int = 50):
    return request.app.state.list_delayed_effects(status, limit)


@router.get("/api/world/events")
def events(request: Request, after_id: int = 0, limit: int = 50, branch_key: str = ""):
    return request.app.state.get_world_events(after_id, limit, branch_key)
