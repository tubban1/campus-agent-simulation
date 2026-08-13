from __future__ import annotations

from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.db import create_database_engine, get_connection
from app.spatial.planner import RouteNotFoundError
from app.spatial.repository import SpatialRepository
from app.world_state.runtime_schema import append_world_event
from app.spatial.runtime import (
    pause_spatial_movement,
    preview_route,
    resume_spatial_movement,
    start_spatial_movement,
)
from app.spatial.schemas import (
    AgentSpatialStateResponse,
    AgentSpatialStatesResponse,
    AdmissionQueueResponse,
    CreateSpatialEventRequest,
    MovementControlRequest,
    OccupancyResponse,
    RoutePlanRequest,
    SceneGraphResponse,
    SetDestinationRequest,
    PhysicalStateMutationRequest,
    SpatialResourcesResponse,
    SpatialWorldsResponse,
    TrajectoryResponse,
)
from app.spatial.service import (
    ResidentNotFoundError,
    SpatialService,
    SpatialStateNotInitializedError,
)


router = APIRouter(tags=["spatial"])


@lru_cache(maxsize=1)
def get_spatial_engine():
    return create_database_engine()


def with_spatial_service(callback):
    engine = get_spatial_engine()
    with engine.connect() as connection:
        return callback(SpatialService(SpatialRepository(connection)))


@router.get("/api/spatial/scene", response_model=SceneGraphResponse)
def get_spatial_scene(
    world_key: Optional[str] = Query(default=None),
    min_x: Optional[float] = Query(default=None),
    min_z: Optional[float] = Query(default=None),
    max_x: Optional[float] = Query(default=None),
    max_z: Optional[float] = Query(default=None),
):
    viewport = (min_x, min_z, max_x, max_z)
    if any(value is not None for value in viewport) and not all(value is not None for value in viewport):
        raise HTTPException(status_code=422, detail="Viewport requires min_x, min_z, max_x and max_z together")
    if all(value is not None for value in viewport) and (min_x >= max_x or min_z >= max_z):
        raise HTTPException(status_code=422, detail="Viewport bounds must have min < max")
    return with_spatial_service(
        lambda service: service.get_scene_graph(
            world_key=world_key,
            min_x=min_x,
            min_z=min_z,
            max_x=max_x,
            max_z=max_z,
        )
    )


@router.get("/api/spatial/physical-states")
def get_spatial_physical_states(world_key: Optional[str] = Query(default=None)):
    """Current factual physical layer used by map rendering and perception."""
    return with_spatial_service(lambda service: service.get_physical_states(world_key=world_key))


@router.post("/api/spatial/physical-states/mutate")
def mutate_spatial_physical_state(payload: PhysicalStateMutationRequest):
    from app.spatial.physical_state_service import apply_spatial_physical_event
    try:
        with get_connection() as connection:
            result = apply_spatial_physical_event(connection, **payload.model_dump(exclude={"title"}))
            append_world_event(
                connection, event_type="spatial_physical_state_changed", title=payload.title,
                content=f"空间物理状态调整为 {payload.access_status}",
                location=payload.world_key, payload=result, source_type="map_interaction",
            )
            connection.commit()
            return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/spatial/worlds", response_model=SpatialWorldsResponse)
def get_spatial_worlds():
    return with_spatial_service(lambda service: service.list_worlds())


@router.get("/api/spatial/occupancy", response_model=OccupancyResponse)
def get_spatial_occupancy():
    return with_spatial_service(lambda service: service.get_occupancy())


@router.get("/api/spatial/resources", response_model=SpatialResourcesResponse)
def get_spatial_resources():
    return with_spatial_service(lambda service: service.get_resources())


@router.get("/api/spatial/admission-queue", response_model=AdmissionQueueResponse)
def get_spatial_admission_queue():
    return with_spatial_service(lambda service: service.get_admission_queue())


@router.get("/api/spatial/agents", response_model=AgentSpatialStatesResponse)
def get_spatial_agents():
    return with_spatial_service(lambda service: service.list_agent_states())


@router.get(
    "/api/agents/{resident_id}/spatial-state",
    response_model=AgentSpatialStateResponse,
)
def get_agent_spatial_state(resident_id: int):
    try:
        return with_spatial_service(
            lambda service: service.get_agent_state(resident_id)
        )
    except ResidentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SpatialStateNotInitializedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/api/agents/{resident_id}/trajectory",
    response_model=TrajectoryResponse,
)
def get_agent_trajectory(
    resident_id: int,
    run_id: Optional[int] = Query(default=None, ge=1),
    branch_key: str = Query(default="main", min_length=1, max_length=80),
    from_tick: Optional[int] = Query(default=None, ge=0),
    to_tick: Optional[int] = Query(default=None, ge=0),
):
    try:
        return with_spatial_service(
            lambda service: service.get_trajectory(
                resident_id,
                experiment_run_id=run_id,
                branch_key=branch_key,
                from_tick=from_tick,
                to_tick=to_tick,
            )
        )
    except ResidentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/agents/{resident_id}/movement/plan")
def plan_agent_movement(resident_id: int, payload: RoutePlanRequest):
    try:
        with get_connection() as connection:
            return preview_route(connection, resident_id, payload.destination)
    except RouteNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/agents/{resident_id}/movement/pause")
def pause_agent_movement(resident_id: int, payload: MovementControlRequest):
    try:
        with get_connection() as connection:
            result = pause_spatial_movement(
                connection,
                resident_id,
                reason=payload.reason,
            )
            connection.commit()
            return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/agents/{resident_id}/movement/resume")
def resume_agent_movement(resident_id: int):
    try:
        with get_connection() as connection:
            result = resume_spatial_movement(connection, resident_id)
            connection.commit()
            return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/agents/{resident_id}/destination")
def set_agent_destination(resident_id: int, payload: SetDestinationRequest):
    try:
        with get_connection() as connection:
            result = start_spatial_movement(
                connection,
                resident_id=resident_id,
                destination=payload.destination,
                constraint_response=payload.constraint_response,
            )
            connection.commit()
            return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/spatial/events")
def create_spatial_event(payload: CreateSpatialEventRequest):
    try:
        with get_connection() as connection:
            desc = payload.description or f"在坐标 ({payload.longitude:.5f}, {payload.latitude:.5f}) 发生【{payload.title}】"
            location_str = f"{payload.world_key} ({payload.longitude:.4f}, {payload.latitude:.4f})"

            event = append_world_event(
                connection,
                event_type=payload.event_type,
                title=payload.title,
                content=desc,
                location=location_str,
                payload={
                    "world_key": payload.world_key,
                    "longitude": payload.longitude,
                    "latitude": payload.latitude,
                },
                source_type="map_interaction",
            )
            connection.commit()
            return {
                "status": "success",
                "event_id": event["id"],
                "world_key": payload.world_key,
                "longitude": payload.longitude,
                "latitude": payload.latitude,
                "event_type": payload.event_type,
                "title": payload.title,
                "description": desc,
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存地图事件失败: {exc}") from exc


@router.get("/api/spatial/affordances")
def get_spatial_affordances_endpoint(
    world_key: Optional[str] = Query(default=None),
    node_id: Optional[int] = Query(default=None),
):
    from app.spatial.affordance_service import get_spatial_affordances
    with get_connection() as connection:
        affordances = get_spatial_affordances(connection, world_key=world_key, node_id=node_id)
        return {"affordances": affordances}


@router.get("/api/agents/{resident_id}/affordance-opportunities")
def discover_agent_affordances_endpoint(resident_id: int):
    from app.spatial.affordance_service import discover_agent_affordance_opportunities
    with get_connection() as connection:
        return discover_agent_affordance_opportunities(connection, resident_id)


@router.get("/api/agents/{resident_id}/action-plan")
def get_agent_action_plan_endpoint(resident_id: int):
    from app.world_runtime.atomic_action_runtime import get_agent_active_plan
    with get_connection() as connection:
        plan = get_agent_active_plan(connection, resident_id)
        if not plan:
            return {"status": "none", "resident_id": resident_id, "plan": None}
        return {"status": "ok", "resident_id": resident_id, "plan": plan}


@router.post("/api/agents/{resident_id}/action-plan")
def create_agent_action_plan_endpoint(
    resident_id: int,
    target_affordance_key: str = Query(...),
    target_node_id: int = Query(...),
    goal_id: Optional[int] = Query(default=None),
):
    from app.world_runtime.atomic_action_runtime import create_agent_action_plan
    with get_connection() as connection:
        plan = create_agent_action_plan(
            connection,
            resident_id=resident_id,
            target_affordance_key=target_affordance_key,
            target_node_id=target_node_id,
            goal_id=goal_id,
        )
        connection.commit()
        return plan


@router.post("/api/agents/{resident_id}/atomic-step/next")
def execute_next_atomic_step_endpoint(resident_id: int):
    from app.world_runtime.atomic_action_runtime import execute_next_atomic_step
    with get_connection() as connection:
        result = execute_next_atomic_step(connection, resident_id)
        connection.commit()
        return result
