"""Household member management endpoints."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.db.session import get_session
from app.models import HouseholdMember, MemberKind
from app.schemas.household import MemberCreate, MemberUpdate

router = APIRouter(prefix="/household", tags=["household"])
DB = Annotated[Session, Depends(get_session)]


@router.get("/members")
def list_members(db: DB):
    return db.exec(select(HouseholdMember).order_by(HouseholdMember.id)).all()


@router.post("/members")
def add_member(req: MemberCreate, db: DB):
    member = HouseholdMember(
        name=req.name,
        kind=MemberKind(req.kind),
        age_months=req.age_months,
        dietary_notes=req.dietary_notes,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


@router.patch("/members/{member_id}")
def update_member(member_id: int, req: MemberUpdate, db: DB):
    member = db.get(HouseholdMember, member_id)
    if not member:
        raise HTTPException(404, "Member not found")
    if req.name is not None:
        member.name = req.name
    if req.kind is not None:
        member.kind = MemberKind(req.kind)
    if req.age_months is not None:
        member.age_months = req.age_months
    if req.dietary_notes is not None:
        member.dietary_notes = req.dietary_notes
    if req.active is not None:
        member.active = req.active
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


@router.delete("/members/{member_id}")
def remove_member(member_id: int, db: DB):
    member = db.get(HouseholdMember, member_id)
    if not member:
        raise HTTPException(404)
    member.active = False
    db.add(member)
    db.commit()
    return {"status": "deactivated"}
