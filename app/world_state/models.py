from typing import Optional

from pydantic import BaseModel, Field


class EnvironmentConfigRequest(BaseModel):
    config_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    config: dict
    parent_config_id: Optional[int] = None
    created_by: str = Field(default="admin", max_length=80)
    activate: bool = False


class WorldSnapshotRequest(BaseModel):
    reason: str = Field(default="manual checkpoint", max_length=240)
    snapshot_type: str = Field(default="manual_checkpoint", max_length=60)
    run_id: str = Field(default="", max_length=120)
    branch_key: str = Field(default="", max_length=80)
    parent_snapshot_id: Optional[int] = None
    external_data_version: str = Field(default="", max_length=120)
    metadata: dict = Field(default_factory=dict)


class WorldSnapshotRestoreRequest(BaseModel):
    reason: str = Field(default="restore checkpoint", max_length=240)
    create_backup: bool = True


class WorldBranchRequest(BaseModel):
    branch_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    name: str = Field(default="", max_length=120)
    source_snapshot_id: int
    metadata: dict = Field(default_factory=dict)


class WorldBranchSwitchRequest(BaseModel):
    reason: str = Field(default="switch branch", max_length=240)
