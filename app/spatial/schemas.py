from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class SpatialNodeResponse(BaseModel):
    id: int
    code: str
    name: str
    node_type: str
    parent_id: Optional[int] = None
    world_key: str = "default"
    x: float
    y: float
    z: float
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    elevation_m: float = 0.0
    geometry_json: Optional[dict[str, Any]] = None
    source_element_id: Optional[str] = None
    radius: float
    capacity: int
    status: str
    properties: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SpatialEdgeResponse(BaseModel):
    id: int
    from_node_id: int
    to_node_id: int
    distance_meters: float
    base_minutes: float
    bidirectional: bool
    status: str
    congestion_factor: float
    weather_factor: float
    properties: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SceneGraphResponse(BaseModel):
    coordinate_system: str
    schema_version: int
    topology_version: str
    world_key: str = "default"
    bounds: Optional[dict[str, float]] = None
    wgs84_bounds: Optional[list[float]] = None
    nodes: list[SpatialNodeResponse]
    edges: list[SpatialEdgeResponse]
    physical_states: list[dict[str, Any]] = Field(default_factory=list)


class SpatialWorldItemResponse(BaseModel):
    world_key: str
    name: str
    node_count: int
    edge_count: int
    is_real_world: bool = False
    metric_bounds: Optional[list[float]] = None
    wgs84_bounds: Optional[list[float]] = None
    source: Optional[str] = None
    license: Optional[str] = None
    imported_at: Optional[str] = None


class SpatialWorldsResponse(BaseModel):
    worlds: list[SpatialWorldItemResponse]


class PhysicalStateMutationRequest(BaseModel):
    world_key: str = Field(min_length=1, max_length=64)
    node_id: Optional[int] = Field(default=None, ge=1)
    edge_id: Optional[int] = Field(default=None, ge=1)
    access_status: str = Field(default="closed", pattern="^(open|restricted|closed)$")
    duration_minutes: int = Field(default=60, ge=1, le=10080)
    title: str = Field(default="地图物理状态更新", min_length=1, max_length=120)


class OccupancyItemResponse(BaseModel):
    node_id: int
    code: str
    name: str
    capacity: int
    occupancy: int
    occupancy_ratio: float = Field(ge=0)


class OccupancyResponse(BaseModel):
    spaces: list[OccupancyItemResponse]


class SpatialResourceResponse(BaseModel):
    id: int
    node_id: int
    node_code: str
    node_name: str
    resource_key: str
    name: str
    capacity: int
    available_units: int
    service_rate_per_hour: float
    status: str
    properties: dict[str, Any]
    updated_at: datetime


class SpatialResourcesResponse(BaseModel):
    resources: list[SpatialResourceResponse]


class AdmissionQueueItemResponse(BaseModel):
    id: int
    resident_id: int
    node_id: int
    node_code: str
    node_name: str
    resource_id: Optional[int] = None
    resource_key: Optional[str] = None
    resource_name: Optional[str] = None
    requested_at: datetime
    queue_position: int
    patience_minutes: float
    estimated_wait_minutes: float
    reason_code: str
    branch_key: str
    requested_tick: int
    status: str
    updated_at: datetime


class AdmissionQueueResponse(BaseModel):
    queue: list[AdmissionQueueItemResponse]


class SpatialCapabilityResponse(BaseModel):
    base_speed_m_per_min: float
    mobility_class: str
    accessibility_needs: dict[str, Any]
    perception_radius_m: float
    hearing_radius_m: float
    source: str
    version: int


class AgentSpatialStateResponse(BaseModel):
    resident_id: int
    current_node_id: int
    origin_node_id: Optional[int] = None
    target_node_id: Optional[int] = None
    x: float
    y: float
    z: float
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    facing_x: float
    facing_z: float
    movement_status: str
    path: list[Any]
    path_index: int
    progress: float
    route_distance_meters: float
    remaining_distance_meters: float
    updated_tick: int
    version: int
    branch_key: str
    planned_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    last_progress_at: Optional[datetime] = None
    estimated_arrival_at: Optional[datetime] = None
    replan_count: int
    last_replan_reason: str
    interrupted_reason: str
    updated_at: datetime
    current_node_code: str
    current_node_name: str
    origin_node_code: Optional[str] = None
    origin_node_name: Optional[str] = None
    target_node_code: Optional[str] = None
    target_node_name: Optional[str] = None
    capability: SpatialCapabilityResponse


class AgentSpatialStatesResponse(BaseModel):
    agents: list[AgentSpatialStateResponse]


class TrajectoryItemResponse(BaseModel):
    id: int
    experiment_run_id: int
    branch_key: str
    tick_number: int
    resident_id: int
    node_id: Optional[int] = None
    x: float
    y: float
    z: float
    movement_status: str
    metadata: dict[str, Any]
    created_at: datetime


class TrajectoryResponse(BaseModel):
    resident_id: int
    experiment_run_id: Optional[int] = None
    branch_key: str
    from_tick: int
    to_tick: int
    trajectory: list[TrajectoryItemResponse]


class RoutePlanRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=120)


class MovementControlRequest(BaseModel):
    reason: str = Field(default="manual_pause", min_length=1, max_length=120)


class SetDestinationRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=120)
    constraint_response: str = Field(default="auto")


class CreateSpatialEventRequest(BaseModel):
    world_key: str = Field(min_length=1, max_length=80)
    longitude: float
    latitude: float
    event_type: str = Field(default="physical_environment_change")
    title: str = Field(min_length=1, max_length=150)
    description: Optional[str] = None


class SpatialAffordanceResponse(BaseModel):
    id: int
    world_key: str
    node_id: int
    node_name: str
    affordance_key: str
    name: str
    requirements: dict[str, Any]
    effects: dict[str, Any]
    capacity: int
    status: str


class SpatialAffordancesResponse(BaseModel):
    affordances: list[SpatialAffordanceResponse]


class AgentActionStep(BaseModel):
    action: str
    target_node_id: Optional[int] = None
    target_node_name: Optional[str] = None
    goal: Optional[str] = None
    expected_cost: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list)
    fallback: Optional[str] = None


class AgentActionPlanResponse(BaseModel):
    id: int
    resident_id: int
    goal_id: Optional[int] = None
    status: str
    target_affordance_key: str
    target_node_id: Optional[int] = None
    current_step_index: int
    steps: list[AgentActionStep]
    created_at: datetime
    updated_at: datetime
