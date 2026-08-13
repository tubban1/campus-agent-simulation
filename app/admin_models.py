from typing import Optional
from pydantic import BaseModel, Field

class AdminWorldEventRequest(BaseModel):
    title: str
    content: str = ""
    event_type: str = "admin_event"
    resident_id: Optional[int] = None
    location: str = ""
    target_spaces: list[str] = Field(default_factory=list)
    intensity: int = Field(default=50, ge=1, le=100)
    payload: dict = Field(default_factory=dict)
