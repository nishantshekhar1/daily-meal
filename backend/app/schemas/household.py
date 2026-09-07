from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class MemberCreate(BaseModel):
    name: str
    kind: str   # adult_male | adult_female | child | toddler
    age_months: Optional[int] = None
    dietary_notes: Optional[str] = None


class MemberUpdate(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    age_months: Optional[int] = None
    dietary_notes: Optional[str] = None
    active: Optional[bool] = None
