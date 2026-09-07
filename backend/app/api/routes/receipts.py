"""Receipt ingestion endpoints."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from app.core.config import get_settings
from app.db.session import get_session
from app.models import Receipt, ReceiptLine, ReceiptStatus, StockLot, StockSource
from app.services.canonicalize import write_alias
from app.services.llm_client import get_llm_client
from app.services.receipt_parser import parse_receipt

router = APIRouter(prefix="/receipts", tags=["receipts"])
DB = Annotated[Session, Depends(get_session)]


@router.post("/upload")
async def upload_receipt(file: UploadFile = File(...), db: DB = Depends(get_session)):
    """Upload a receipt image.  Triggers OCR + parsing; returns the review payload."""
    settings = get_settings()
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "receipt.jpg").suffix or ".jpg"
    dest = upload_dir / f"receipt_{uuid.uuid4()}{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    receipt = Receipt(image_path=str(dest))
    db.add(receipt)
    db.commit()
    db.refresh(receipt)

    llm = get_llm_client()
    lines = await parse_receipt(receipt, dest, db, llm)
    db.refresh(receipt)

    return {
        "receipt_id": receipt.id,
        "status": receipt.status,
        "store_name": receipt.store_name,
        "lines": [
            {
                "id": l.id,
                "raw_text": l.raw_text,
                "is_grocery": l.is_grocery,
                "quantity": l.quantity,
                "unit": l.unit,
                "ingredient_id": l.ingredient_id,
                "match_confidence": l.match_confidence,
                "confirmed": l.confirmed,
            }
            for l in lines
        ],
    }


@router.get("/{receipt_id}")
def get_receipt(receipt_id: int, db: DB):
    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        raise HTTPException(404, "Receipt not found")
    lines = db.exec(select(ReceiptLine).where(ReceiptLine.receipt_id == receipt_id)).all()
    return {"receipt": receipt, "lines": lines}


@router.post("/{receipt_id}/confirm")
def confirm_receipt(receipt_id: int, db: DB):
    """Commit all confirmed receipt lines to the pantry (create StockLots).

    Expects that individual lines have been updated via PATCH /receipts/{id}/lines/{lid}
    before this call.
    """
    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        raise HTTPException(404, "Receipt not found")

    stmt = (
        select(ReceiptLine)
        .where(ReceiptLine.receipt_id == receipt_id)
        .where(ReceiptLine.confirmed == True)
        .where(ReceiptLine.is_grocery == True)
        .where(ReceiptLine.ingredient_id != None)
    )
    lines = db.exec(stmt).all()

    created = []
    from datetime import datetime
    for line in lines:
        lot = StockLot(
            ingredient_id=line.ingredient_id,
            quantity=line.quantity or 1.0,
            unit=line.unit or "count",
            original_quantity=line.quantity or 1.0,
            source=StockSource.receipt,
            receipt_line_id=line.id,
            acquired_at=datetime.utcnow(),
        )
        db.add(lot)
        db.flush()
        created.append(lot.id)

    receipt.status = ReceiptStatus.confirmed
    db.add(receipt)
    db.commit()
    return {"status": "confirmed", "lots_created": created}


@router.patch("/{receipt_id}/lines/{line_id}")
def update_line(
    receipt_id: int,
    line_id: int,
    payload: dict,
    db: DB,
):
    """User edits a receipt line on the review screen (change ingredient, qty, confirm)."""
    line = db.get(ReceiptLine, line_id)
    if not line or line.receipt_id != receipt_id:
        raise HTTPException(404, "Line not found")

    if "ingredient_id" in payload:
        line.ingredient_id = payload["ingredient_id"]
    if "quantity" in payload:
        line.quantity = float(payload["quantity"])
    if "unit" in payload:
        line.unit = payload["unit"]
    if "confirmed" in payload:
        line.confirmed = bool(payload["confirmed"])
    if "is_grocery" in payload:
        line.is_grocery = bool(payload["is_grocery"])

    db.add(line)
    db.commit()
    return {"status": "updated", "line_id": line_id}


@router.delete("/{receipt_id}")
def discard_receipt(receipt_id: int, db: DB):
    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        raise HTTPException(404)
    receipt.status = ReceiptStatus.rejected
    db.add(receipt)
    db.commit()
    return {"status": "discarded"}
