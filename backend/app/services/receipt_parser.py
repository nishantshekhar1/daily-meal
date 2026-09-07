"""Receipt parsing service.

Pipeline:
  1. OCR via PaddleOCR-VL (produces raw text per line)
  2. Line classification: grocery vs. non-grocery via LLM
  3. Quantity + unit extraction from each grocery line
  4. Canonicalization (delegates to canonicalize.py)
"""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Optional

from sqlmodel import Session

from app.models import Receipt, ReceiptLine, ReceiptStatus
from app.services.canonicalize import canonicalize
from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

# JSON schema for the grocery-classification LLM call
_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "raw_text": {"type": "string"},
                    "is_grocery": {"type": "boolean"},
                    "quantity": {"type": ["number", "null"]},
                    "unit": {"type": ["string", "null"]},
                    "item_name": {"type": ["string", "null"]},
                },
                "required": ["raw_text", "is_grocery", "quantity", "unit", "item_name"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lines"],
    "additionalProperties": False,
}


async def parse_receipt(
    receipt: Receipt,
    image_path: Path,
    session: Session,
    llm: LLMClient,
) -> list[ReceiptLine]:
    """Full parsing pass.  Returns the list of ReceiptLine objects added to session."""

    # ── Stage 1: OCR ─────────────────────────────────────────────────────────
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()
    suffix = image_path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"

    raw_text = await llm.ocr(image_b64, mime)
    receipt.raw_ocr_text = raw_text
    session.add(receipt)

    raw_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not raw_lines:
        return []

    # ── Stage 2: classify lines via LLM ──────────────────────────────────────
    messages = [
        {
            "role": "system",
            "content": (
                "You parse grocery receipt lines. For each line provided, decide if it is "
                "a grocery/food item (is_grocery=true) or something else (tax, total, "
                "non-food product, store code, etc.). "
                "For grocery lines, extract quantity (numeric) and unit (g, kg, mL, L, oz, "
                "lb, count, pack, etc.) if visible; otherwise null. "
                "Also extract the item_name with receipt codes and brand names removed. "
                "Return strict JSON matching the schema."
            ),
        },
        {
            "role": "user",
            "content": "Receipt lines:\n" + "\n".join(f"- {l}" for l in raw_lines),
        },
    ]

    classified: dict = {}
    try:
        classified = await llm.chat_json(messages, _CLASSIFY_SCHEMA, temperature=0.0)
    except Exception as exc:
        logger.warning("Receipt classification failed: %s", exc)

    classified_lines: list[dict] = classified.get("lines", [])

    # ── Stage 3: canonicalize grocery lines ──────────────────────────────────
    result: list[ReceiptLine] = []
    for cl in classified_lines:
        if not cl.get("is_grocery"):
            rl = ReceiptLine(
                receipt_id=receipt.id,
                raw_text=cl["raw_text"],
                is_grocery=False,
            )
            session.add(rl)
            result.append(rl)
            continue

        item_name = cl.get("item_name") or cl["raw_text"]
        quantity = cl.get("quantity")
        unit = cl.get("unit") or "count"

        ing, confidence = await canonicalize(item_name, session, llm)

        rl = ReceiptLine(
            receipt_id=receipt.id,
            raw_text=cl["raw_text"],
            is_grocery=True,
            quantity=quantity,
            unit=unit,
            ingredient_id=ing.id if ing else None,
            match_confidence=confidence,
        )
        session.add(rl)
        result.append(rl)

    receipt.status = ReceiptStatus.reviewing
    session.add(receipt)
    session.commit()
    return result
