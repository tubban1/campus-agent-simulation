from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AgentBodyStateResponse(BaseModel):
    resident_id: int
    resident_name: str
    role: str
    location: str
    hunger: float = Field(ge=0, le=100)
    fatigue: float = Field(ge=0, le=100)
    sleep_debt: float = Field(ge=0, le=100)
    stress: float = Field(ge=0, le=100)
    attention: float = Field(ge=0, le=100)
    social_energy: float = Field(ge=0, le=100)
    health: float = Field(ge=0, le=100)
    weather_exposure: float = Field(ge=0, le=100)
    hydration: float = Field(ge=0, le=100)
    nutrition: float = Field(ge=0, le=100)
    activity_load: float = Field(ge=0, le=100)
    illness_load: float = Field(ge=0, le=100)
    sleep_state: str = "awake"
    energy: int = Field(ge=0, le=100)
    last_updated_at: Optional[datetime] = None
    last_updated_tick: int = Field(ge=0)
    source: str
    version: int = Field(gt=0)
    updated_at: datetime
    alerts: list[str]


class AgentBodyStatesResponse(BaseModel):
    agents: list[AgentBodyStateResponse]
