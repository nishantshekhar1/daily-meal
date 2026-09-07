from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class AddStockRequest(BaseModel):
    ingredient_id: int
    quantity: float
    unit: str
    notes: Optional[str] = None


class RemoveLotRequest(BaseModel):
    lot_id: int


class IngredientSearchResult(BaseModel):
    id: int
    name: str
    category: str
    default_unit: str


class ConfirmAliasRequest(BaseModel):
    raw_text: str
    ingredient_id: int
