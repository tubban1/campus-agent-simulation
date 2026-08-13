from typing import Optional

from pydantic import BaseModel, Field


class CampusEnvironmentRequest(BaseModel):
    weather: Optional[str] = None
    semester_stage: Optional[str] = None
    time_slot: Optional[str] = None
    weekday: Optional[str] = None
    real_date: Optional[str] = None
    real_time: Optional[str] = None
    time_source: Optional[str] = None
    temperature: Optional[int] = Field(default=None, ge=-20, le=45)
    rainfall: Optional[int] = Field(default=None, ge=0, le=100)
    exam_pressure: Optional[int] = Field(default=None, ge=0, le=100)
    assignment_pressure: Optional[int] = Field(default=None, ge=0, le=100)
    study_atmosphere: Optional[int] = Field(default=None, ge=0, le=100)
    activity_heat: Optional[int] = Field(default=None, ge=0, le=100)
    event_name: Optional[str] = None
    event_intensity: Optional[int] = Field(default=None, ge=0, le=100)
    campus_flow: Optional[int] = Field(default=None, ge=0, le=100)
    classroom_crowd: Optional[int] = Field(default=None, ge=0, le=100)
    canteen_crowd: Optional[int] = Field(default=None, ge=0, le=100)
    library_crowd: Optional[int] = Field(default=None, ge=0, le=100)
    dorm_crowd: Optional[int] = Field(default=None, ge=0, le=100)
    playground_crowd: Optional[int] = Field(default=None, ge=0, le=100)
    commercial_crowd: Optional[int] = Field(default=None, ge=0, le=100)
    traffic_status: Optional[str] = None
    network_status: Optional[str] = None
    safety_level: Optional[int] = Field(default=None, ge=0, le=100)
    resource_pressure: Optional[int] = Field(default=None, ge=0, le=100)
    campus_mood: Optional[str] = None
    consumption_index: Optional[float] = Field(default=None, ge=0.1, le=3.0)


class CampusEventRequest(BaseModel):
    title: str
    event_type: str = "校园活动"
    intensity: int = Field(default=50, ge=1, le=100)
    target_spaces: list[str] = Field(default_factory=list)
    effects: dict = Field(default_factory=dict)


class SpaceStatusRequest(BaseModel):
    status: str
