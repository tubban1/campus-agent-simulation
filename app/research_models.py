from pydantic import BaseModel, Field

class CalibrationObservationRequest(BaseModel):
    source_name: str = "manual"
    observed_at: str = ""
    metric_name: str
    metric_value: float
    location: str = ""
    role_group: str = ""
    sample_size: int = Field(default=0, ge=0)
    metadata: dict = Field(default_factory=dict)
