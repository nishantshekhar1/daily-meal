"""Pantry management service.

Handles:
- Stock lot creation (manual, photo-based)
- Lot-aware quantity aggregation for display
- Low-stock detection (for exhaustion candidate proposals)
- Manual stock removal
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from app.core.config import get_settings
from app.models import CanonicalIngredient, StockLot, StockSource
from app.services.canonicalize import canonicalize, write_alias
from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)


def get_pantry_summary(db: Session) -> list[dict]:
    """Return per-ingredient aggregated quantities for the UI."""
    stmt = (
        select(StockLot, CanonicalIngredient)
        .join(CanonicalIngredient, StockLot.ingredient_id == CanonicalIngredient.id)
        .where(StockLot.quantity > 0)
        .order_by(CanonicalIngredient.name)
    )
    rows = db.exec(stmt).all()

    summary: dict[int, dict] = {}
    for lot, ing in rows:
        if ing.id not in summary:
            summary[ing.id] = {
                "ingredient_id": ing.id,
                "name": ing.name,
                "category": ing.category,
                "default_unit": ing.default_unit,
                "total_quantity": 0.0,
                "unit": lot.unit,
                "lots": [],
                "toddler_flags": {
                    "high_sodium": ing.toddler_high_sodium,
                    "choking_risk": ing.toddler_choking_risk,
                    "forbidden_under_months": ing.toddler_forbidden_under_months,
                },
            }
        summary[ing.id]["total_quantity"] += lot.quantity
        summary[ing.id]["lots"].append({
            "lot_id": lot.id,
            "quantity": lot.quantity,
            "unit": lot.unit,
            "source": lot.source,
            "acquired_at": lot.acquired_at.isoformat(),
        })

    return list(summary.values())


def add_stock_manual(
    db: Session,
    ingredient_id: int,
    quantity: float,
    unit: str,
    notes: Optional[str] = None,
) -> StockLot:
    lot = StockLot(
        ingredient_id=ingredient_id,
        quantity=quantity,
        unit=unit,
        original_quantity=quantity,
        source=StockSource.manual,
        notes=notes,
        acquired_at=datetime.utcnow(),
    )
    db.add(lot)
    db.commit()
    db.refresh(lot)
    return lot


async def add_stock_from_photo(
    db: Session,
    llm: LLMClient,
    image_path: Path,
) -> tuple[Optional[StockLot], dict]:
    """Analyse an ingredient photo, identify the ingredient, create a draft lot.

    Returns (lot_or_None, info_dict) where info_dict has keys:
      - ingredient_name: what the model thinks this is
      - quantity, unit: best-effort extraction
      - confidence: float
      - message: human-readable note
    """
    settings = get_settings()
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    suffix = image_path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"

    prompt = (
        "Look at this image of a food ingredient or grocery item. "
        "Identify: (1) the ingredient name, (2) approximate quantity if visible on packaging, "
        "(3) the unit (g, kg, mL, L, count, etc.). "
        "Respond in JSON: {\"ingredient\": str, \"quantity\": number_or_null, \"unit\": str_or_null}"
    )
    raw = await llm.vision_chat(prompt, b64, mime)

    import json
    try:
        parsed = json.loads(raw)
    except Exception:
        return None, {"message": "Could not parse vision model response", "raw": raw}

    ing_name = parsed.get("ingredient", "")
    quantity = parsed.get("quantity") or 1.0
    unit = parsed.get("unit") or "count"

    ing, confidence = await canonicalize(ing_name, db, llm)
    if ing is None:
        return None, {
            "ingredient_name": ing_name,
            "confidence": confidence,
            "message": "Could not match to a known ingredient — please add manually.",
        }

    lot = StockLot(
        ingredient_id=ing.id,
        quantity=float(quantity),
        unit=unit,
        original_quantity=float(quantity),
        source=StockSource.photo,
        image_path=str(image_path),
        acquired_at=datetime.utcnow(),
    )
    db.add(lot)
    db.commit()
    db.refresh(lot)
    return lot, {
        "ingredient_name": ing.name,
        "quantity": quantity,
        "unit": unit,
        "confidence": confidence,
        "message": "Added to pantry (pending your confirmation).",
    }


def remove_stock_lot(db: Session, lot_id: int) -> bool:
    lot = db.get(StockLot, lot_id)
    if lot is None:
        return False
    lot.quantity = 0.0
    db.add(lot)
    db.commit()
    return True


def get_low_stock(db: Session, threshold_pct: Optional[int] = None) -> list[dict]:
    """Return lots where remaining quantity is below threshold_pct of original."""
    settings = get_settings()
    pct = threshold_pct if threshold_pct is not None else settings.low_stock_threshold_pct
    stmt = (
        select(StockLot, CanonicalIngredient)
        .join(CanonicalIngredient, StockLot.ingredient_id == CanonicalIngredient.id)
        .where(StockLot.original_quantity > 0)
        .where(StockLot.quantity > 0)
    )
    rows = db.exec(stmt).all()
    result = []
    for lot, ing in rows:
        remaining_pct = (lot.quantity / lot.original_quantity) * 100
        if remaining_pct <= pct:
            result.append({
                "lot_id": lot.id,
                "ingredient_id": ing.id,
                "ingredient_name": ing.name,
                "quantity": lot.quantity,
                "original_quantity": lot.original_quantity,
                "unit": lot.unit,
                "remaining_pct": round(remaining_pct, 1),
            })
    return result
