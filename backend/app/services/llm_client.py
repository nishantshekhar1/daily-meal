"""LLM client abstraction.

All communication with local LLM endpoints (Ollama OpenAI-compatible API) goes through this module.
The backing model is a config value — swap roles in config/models.yaml,
no code changes needed.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _make_client(cfg: dict[str, Any]) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=cfg["base_url"],
        api_key=cfg.get("api_key", "not-needed"),
        http_client=httpx.AsyncClient(timeout=120.0),
    )


class LLMClient:
    """Thin async wrapper around the OpenAI-compatible Ollama server.

    Three entry points:
    - chat()           → free-form chat, returns the assistant text
    - chat_json()      → constrained JSON output via response_format
    - chat_tools()     → tool-calling for the agent loop
    - embed()          → embedding vector for canonicalization
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._reasoning_cfg = settings.reasoning_cfg
        self._vision_cfg = settings.vision_cfg
        self._ocr_cfg = settings.ocr_cfg
        self._embedding_cfg = settings.embedding_cfg

        self._reasoning = _make_client(self._reasoning_cfg)
        # vision may point at the same server; reuse if URLs match
        if settings.vision_cfg.get("base_url") == settings.reasoning_cfg.get("base_url"):
            self._vision = self._reasoning
        else:
            self._vision = _make_client(self._vision_cfg)
        self._ocr = _make_client(self._ocr_cfg)
        self._embedding_client = _make_client(self._embedding_cfg)

    # ── Reasoning / text ────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def chat(self, messages: list[dict], **kwargs) -> str:
        cfg = self._reasoning_cfg
        resp = await self._reasoning.chat.completions.create(
            model=cfg["model"],
            messages=messages,
            max_tokens=cfg.get("max_tokens", 2048),
            temperature=kwargs.get("temperature", cfg.get("temperature", 0.4)),
        )
        return resp.choices[0].message.content or ""

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def chat_json(self, messages: list[dict], schema: dict, **kwargs) -> dict:
        """Request structured JSON output matching `schema` (JSON Schema object).

        Tries OpenAI json_schema first; falls back to json_object + prompt for Ollama.
        """
        cfg = self._reasoning_cfg
        content = "{}"
        try:
            resp = await self._reasoning.chat.completions.create(
                model=cfg["model"],
                messages=messages,
                max_tokens=cfg.get("max_tokens", 2048),
                temperature=kwargs.get("temperature", 0.2),
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": schema, "strict": True},
                },
            )
            content = resp.choices[0].message.content or "{}"
        except Exception as e:
            logger.info("json_schema unsupported (%s); falling back to json_object", e)
            hint = {
                "role": "system",
                "content": (
                    "Respond with a single JSON object only (no markdown). "
                    f"It must match this JSON Schema: {json.dumps(schema)}"
                ),
            }
            resp = await self._reasoning.chat.completions.create(
                model=cfg["model"],
                messages=[hint, *messages],
                max_tokens=cfg.get("max_tokens", 2048),
                temperature=kwargs.get("temperature", 0.2),
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or "{}"
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning("JSON parse failed, raw content: %s", content[:500])
            return {}

    async def chat_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        max_iterations: int = 10,
    ) -> tuple[str, list[dict]]:
        """Run a bounded tool-calling loop.

        Returns (final_text, call_log) where call_log is a list of
        {tool_name, arguments, result} dicts for observability.
        """
        cfg = self._reasoning_cfg
        history = list(messages)
        call_log: list[dict] = []

        for _ in range(max_iterations):
            resp = await self._reasoning.chat.completions.create(
                model=cfg["model"],
                messages=history,
                tools=tools,
                tool_choice="auto",
                max_tokens=cfg.get("max_tokens", 4096),
                temperature=cfg.get("temperature", 0.4),
            )
            msg = resp.choices[0].message
            history.append(msg.model_dump(exclude_unset=True))

            if not msg.tool_calls:
                return msg.content or "", call_log

            # Execute each tool call (caller must register tools externally)
            for tc in msg.tool_calls:
                call_log.append({
                    "tool_name": tc.function.name,
                    "arguments": tc.function.arguments,
                    "result": "__pending__",
                })
                # Tool results are injected by the agent layer, not here.
                # Return the pending call so the agent can resolve it.
                return "", call_log

        return "", call_log

    # ── Vision ───────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def vision_chat(self, prompt: str, image_b64: str, mime: str = "image/jpeg") -> str:
        """Send a single image with a text prompt to the vision model."""
        cfg = self._vision_cfg
        resp = await self._vision.chat.completions.create(
            model=cfg["model"],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            max_tokens=cfg.get("max_tokens", 1024),
            temperature=cfg.get("temperature", 0.1),
        )
        return resp.choices[0].message.content or ""

    # ── OCR ──────────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def ocr(self, image_b64: str, mime: str = "image/jpeg") -> str:
        """Run receipt OCR via the configured vision/OCR model. Returns raw text."""
        cfg = self._ocr_cfg
        prompt = (
            "Extract all text from this grocery receipt exactly as printed. "
            "Output each line of the receipt on its own line. "
            "Do not add any commentary."
        )
        resp = await self._ocr.chat.completions.create(
            model=cfg["model"],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            max_tokens=cfg.get("max_tokens", 2048),
            temperature=0.0,
        )
        return resp.choices[0].message.content or ""

    # ── Embeddings ────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for each text string."""
        cfg = self._embedding_cfg
        resp = await self._embedding_client.embeddings.create(
            model=cfg["model"],
            input=texts,
        )
        return [item.embedding for item in resp.data]


# Module-level singleton – created once per process
_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
