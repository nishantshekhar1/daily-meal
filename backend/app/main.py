"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import household, meals, pantry, receipts
from app.core.config import get_settings
from app.db.session import create_db_and_tables

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(settings.upload_dir, exist_ok=True)
    # In dev: create tables if Alembic hasn't been run yet
    create_db_and_tables()
    logger.info("Daily Meal Planner API started. DB: %s", settings.db_url)
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="Daily Meal Planner",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restrict to LAN in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pantry.router, prefix="/api/v1")
app.include_router(receipts.router, prefix="/api/v1")
app.include_router(meals.router, prefix="/api/v1")
app.include_router(household.router, prefix="/api/v1")


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}
