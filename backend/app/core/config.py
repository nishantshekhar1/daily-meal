"""Application configuration loaded from environment + models.yaml."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    config_path: Path = Path("../config/models.yaml")
    data_dir: Path = Path("./data")
    upload_dir: Path = Path("./uploads")
    log_level: str = "INFO"

    # Resolved from models.yaml at startup
    _models_cfg: dict[str, Any] = {}

    @model_validator(mode="after")
    def _load_models_yaml(self) -> "Settings":
        path = self.config_path
        if path.exists():
            with open(path) as f:
                self._models_cfg = yaml.safe_load(f) or {}
        return self

    def _role(self, role: str) -> dict[str, Any]:
        roles: dict = self._models_cfg.get("roles", {})
        cfg = dict(roles.get(role, {}))
        # Prefer ollama_host; fall back to legacy gpu_box_host key.
        host = self._models_cfg.get(
            "ollama_host",
            self._models_cfg.get("gpu_box_host", "127.0.0.1"),
        )
        if "base_url" in cfg:
            cfg["base_url"] = (
                cfg["base_url"]
                .replace("{ollama_host}", host)
                .replace("{gpu_box_host}", host)
            )
        return cfg

    @property
    def reasoning_cfg(self) -> dict[str, Any]:
        return self._role("reasoning")

    @property
    def vision_cfg(self) -> dict[str, Any]:
        return self._role("vision")

    @property
    def ocr_cfg(self) -> dict[str, Any]:
        return self._role("ocr")

    @property
    def embedding_cfg(self) -> dict[str, Any]:
        return self._role("embedding")

    @property
    def features(self) -> dict[str, Any]:
        return self._models_cfg.get("features", {})

    @property
    def web_search_enabled(self) -> bool:
        return bool(self.features.get("web_search", False))

    @property
    def searxng_url(self) -> str:
        return str(self.features.get("searxng_url", "http://localhost:8080"))

    @property
    def preferences(self) -> dict[str, Any]:
        return self._models_cfg.get("preferences", {})

    @property
    def cuisine_priority(self) -> list[str]:
        """Cuisines in descending preference order, normalized to lowercase.

        Dishes tagged with a cuisine not in this list rank after all listed ones.
        """
        raw = self.preferences.get("cuisine_priority") or []
        return [str(c).strip().lower() for c in raw if str(c).strip()]

    @property
    def max_suggestions(self) -> int:
        """How many recipes one plan contains (one LLM generation each)."""
        return max(1, int(self.preferences.get("max_suggestions", 3)))

    @property
    def prewarm_suggestions(self) -> bool:
        return bool(self.features.get("prewarm_suggestions", True))

    @property
    def prewarm_interval_s(self) -> int:
        return max(30, int(self.features.get("prewarm_interval_s", 300)))

    @property
    def app_cfg(self) -> dict[str, Any]:
        return self._models_cfg.get("app", {})

    @property
    def db_url(self) -> str:
        default = f"sqlite:///{self.data_dir}/daily_meal.db"
        return str(self.app_cfg.get("db_url", default))

    @property
    def low_stock_threshold_pct(self) -> int:
        return int(self.app_cfg.get("low_stock_threshold_pct", 15))

    @property
    def planning_session_ttl_s(self) -> int:
        return int(self.app_cfg.get("planning_session_ttl_s", 3600))


@lru_cache
def get_settings() -> Settings:
    return Settings()
