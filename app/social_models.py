from typing import Optional
from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    speaker_id: int
    listener_id: int
    message: str

class NegotiateRequest(BaseModel):
    initiator_id: int
    target_id: int
    topic: str
    proposal: str

class CollaborateRequest(BaseModel):
    leader_id: int
    member_ids: list[int] = Field(default_factory=list)
    title: str
    goal: str

class CompeteRequest(BaseModel):
    participant_ids: list[int]
    title: str
    metric: str = "综合表现"

class LongTermGoalRequest(BaseModel):
    resident_id: int
    title: str
    category: str = "general"
    deadline_day: Optional[int] = None

class GroupGoalRequest(BaseModel):
    name: str
    group_type: str = "临时小组"
    leader_id: int
    member_ids: list[int] = Field(default_factory=list)
    shared_goal: str
    deadline_day: Optional[int] = None
    current_plan: str = "成员根据分工推进任务，并在每日模拟后汇总进度。"
