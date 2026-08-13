from pydantic import BaseModel, Field

class MoveRequest(BaseModel):
    resident_id: int
    destination: str

class BuySellRequest(BaseModel):
    buyer_id: int
    seller_id: int
    item_name: str
    quantity: int = Field(gt=0)
    unit_price: int = Field(gt=0)
