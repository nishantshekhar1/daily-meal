"""Pantry REST endpoints."""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from app.core.config import get_settings
from app.db.session import get_session
from app.models import CanonicalIngredient, StockLot
from app.schemas.pantry import AddStockRequest, ConfirmAliasRequest, IngredientSearchResult
from app.services import pantry as pantry_svc
from app.services.canonicalize import write_alias
from app.services.llm_client import get_llm_client

router = APIRouter(prefix="/pantry", tags=["pantry"])
DB = Annotated[Session, Depends(get_session)]


@router.get("/")
def list_pantry(db: DB):
    """All current stock, grouped by ingredient."""
    return pantry_svc.get_pantry_summary(db)


@router.post("/stock")
def add_stock(req: AddStockRequest, db: DB):
    lot = pantry_svc.add_stock_manual(db, req.ingredient_id, req.quantity, req.unit, req.notes)
    return {"lot_id": lot.id, "status": "added"}


@router.delete("/stock/{lot_id}")
def remove_lot(lot_id: int, db: DB):
    ok = pantry_svc.remove_stock_lot(db, lot_id)
    if not ok:
        raise HTTPException(404, "Lot not found")
    return {"status": "removed"}


@router.get("/low-stock")
def low_stock(db: DB):
    """Lots near exhaustion."""
    return pantry_svc.get_low_stock(db)


@router.post("/photo")
async def add_from_photo(db: DB, file: UploadFile = File(...)):
    """Identify an ingredient from a photo and create a draft stock lot."""
    settings = get_settings()
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "upload.jpg").suffix or ".jpg"
    dest = upload_dir / f"{uuid.uuid4()}{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    llm = get_llm_client()
    lot, info = await pantry_svc.add_stock_from_photo(db, llm, dest)
    return {"lot_id": lot.id if lot else None, **info}


@router.get("/ingredients/search")
def search_ingredients(q: str, db: DB) -> list[IngredientSearchResult]:
    """Full-text search over canonical ingredient names (for the manual-add UI)."""
    stmt = select(CanonicalIngredient).where(
        CanonicalIngredient.name.contains(q.lower())
    )
    results = db.exec(stmt).all()
    return [
        IngredientSearchResult(
            id=r.id,
            name=r.name,
            category=r.category,
            default_unit=r.default_unit,
        )
        for r in results
    ]


@router.get("/ingredients")
def list_ingredients(db: DB):
    """All canonical ingredients (for the picker dropdown)."""
    rows = db.exec(select(CanonicalIngredient).order_by(CanonicalIngredient.name)).all()
    return [{"id": r.id, "name": r.name, "category": r.category, "default_unit": r.default_unit} for r in rows]


@router.post("/ingredients/confirm-alias")
async def confirm_alias(req: ConfirmAliasRequest, db: DB):
    """User confirms that raw_text maps to ingredient_id.  Writes the alias."""
    ing = db.get(CanonicalIngredient, req.ingredient_id)
    if ing is None:
        raise HTTPException(404, "Ingredient not found")
    alias = write_alias(req.raw_text, ing, db, source="user", confidence=1.0)
    db.commit()
    return {"alias": alias.alias, "ingredient": ing.name}
