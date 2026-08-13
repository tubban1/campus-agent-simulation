from pydantic import BaseModel

class PolicyRequest(BaseModel):
    proposer_id: int
    title: str
    description: str

class VotePolicyRequest(BaseModel):
    resident_id: int
    policy_id: int
    vote: str
