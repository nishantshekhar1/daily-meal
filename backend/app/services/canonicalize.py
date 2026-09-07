"""Ingredient canonicalization pipeline.

Input:  a raw text string (from OCR, manual entry, or photo caption)
Output: a CanonicalIngredient (or None if unresolvable after fallbacks)

Pipeline stages:
  1. Exact alias match (fast, DB lookup)
  2. Embedding nearest-neighbour (semantic match)
  3. LLM fallback (for low-confidence or unrecognized inputs)
  4. User confirmation — callers must surface this; confirmed mappings are
     written back as aliases so future lookups skip stages 2–3.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Optional

import numpy as np
from sqlmodel import Session, select

from app.models import CanonicalIngredient, IngredientAlias
from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

EMBED_THRESHOLD = 0.82      # cosine similarity below this triggers LLM fallback
LLM_CONFIDENCE = 0.75       # confidence assigned to LLM-matched aliases
ALIAS_MIN_CONFIDENCE = 0.60 # below this, always ask the user


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = (np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


async def canonicalize(
    raw: str,
    session: Session,
    llm: LLMClient,
) -> tuple[Optional[CanonicalIngredient], float]:
    """Return (ingredient, confidence).  confidence in [0, 1].

    confidence == 1.0  → exact alias match (human-confirmed)
    confidence >= 0.82 → embedding match, likely correct
    confidence >= 0.60 → LLM match, show to user for confirmation
    confidence < 0.60  → unresolvable; caller should ask user
    """
    cleaned = _clean(raw)

    # Stage 1: exact alias lookup
    stmt = select(IngredientAlias).where(IngredientAlias.alias == cleaned)
    alias = session.exec(stmt).first()
    if alias and alias.confidence >= ALIAS_MIN_CONFIDENCE:
        ing = session.get(CanonicalIngredient, alias.ingredient_id)
        return ing, alias.confidence

    # Stage 2: embedding nearest-neighbour
    all_aliases = session.exec(select(IngredientAlias)).all()
    if all_aliases:
        try:
            query_vec, *alias_vecs = await llm.embed([cleaned] + [a.alias for a in all_aliases])
            sims = [_cosine(query_vec, av) for av in alias_vecs]
            best_idx = int(np.argmax(sims))
            best_sim = sims[best_idx]
            if best_sim >= EMBED_THRESHOLD:
                best_alias = all_aliases[best_idx]
                ing = session.get(CanonicalIngredient, best_alias.ingredient_id)
                return ing, best_sim
        except Exception as exc:
            logger.warning("Embedding call failed: %s", exc)

    # Stage 3: LLM fallback
    all_names = [
        r.name for r in session.exec(select(CanonicalIngredient)).all()
    ]
    if not all_names:
        return None, 0.0

    prompt_messages = [
        {
            "role": "system",
            "content": (
                "You are an ingredient matcher. Given a raw grocery item text, "
                "respond with ONLY the best-matching canonical ingredient name from "
                "the provided list, exactly as written. If nothing matches well, "
                'respond with the single word "UNKNOWN".'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Raw text: {raw!r}\n\n"
                f"Canonical ingredients:\n" + "\n".join(f"- {n}" for n in all_names)
            ),
        },
    ]
    try:
        result = await llm.chat(prompt_messages, temperature=0.0)
        matched_name = result.strip().strip('"').strip("'")
        if matched_name and matched_name.upper() != "UNKNOWN":
            stmt2 = select(CanonicalIngredient).where(
                CanonicalIngredient.name == matched_name
            )
            ing = session.exec(stmt2).first()
            if ing:
                return ing, LLM_CONFIDENCE
    except Exception as exc:
        logger.warning("LLM canonicalization failed: %s", exc)

    return None, 0.0


def write_alias(
    raw: str,
    ingredient: CanonicalIngredient,
    session: Session,
    source: str = "user",
    confidence: float = 1.0,
) -> IngredientAlias:
    """Persist a confirmed alias mapping so future lookups hit Stage 1."""
    cleaned = _clean(raw)
    stmt = select(IngredientAlias).where(IngredientAlias.alias == cleaned)
    existing = session.exec(stmt).first()
    if existing:
        existing.confidence = confidence
        existing.source = source
        session.add(existing)
        return existing
    alias = IngredientAlias(
        alias=cleaned,
        ingredient_id=ingredient.id,
        confidence=confidence,
        source=source,
    )
    session.add(alias)
    return alias


def _clean(text: str) -> str:
    """Normalise raw text for alias comparison."""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    # Strip common receipt artifacts: prices, quantities at start/end
    text = re.sub(r"^\d+\s*x\s*", "", text)  # "2x milk" → "milk"
    text = re.sub(r"\s+\$?\d+[\.,]\d+\s*$", "", text)  # trailing price
    return text.strip()
