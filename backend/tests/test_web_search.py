"""Web search: the enable flag, provider dispatch, auth, and failure fallback.

The HTTP layer is stubbed, so these never touch the network or need a real
API key.  Tests are sync and drive coroutines with ``asyncio.run`` to avoid a
pytest-asyncio dependency.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.agents import tools
from app.core.config import get_settings

TAVILY_PAYLOAD = {
    "results": [
        {"title": "Chana Masala", "url": "https://x/1", "content": "chickpea curry " * 40},
        {"title": "Rajma", "url": "https://x/2", "content": "kidney bean curry"},
    ]
}
SEARXNG_PAYLOAD = {
    "results": [{"title": "Searxng Dal", "url": "https://y/1", "content": "lentil stew"}]
}


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _RecordingClient:
    """Stands in for httpx.AsyncClient and records the outgoing request."""

    seen: dict[str, Any] = {}

    def __init__(self, *_a, **kw) -> None:
        type(self).seen["timeout"] = kw.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, url, headers=None, json=None):
        type(self).seen.update(method="POST", url=url, headers=headers or {}, body=json)
        return _Response(TAVILY_PAYLOAD)

    async def get(self, url, params=None):
        type(self).seen.update(method="GET", url=url, params=params)
        return _Response(SEARXNG_PAYLOAD)


class _FailingClient(_RecordingClient):
    async def post(self, *_a, **_kw):
        raise httpx.ConnectTimeout("simulated timeout")


@pytest.fixture
def http(monkeypatch):
    """Swap in the recording client and hand back its request log."""
    _RecordingClient.seen = {}
    monkeypatch.setattr(tools.httpx, "AsyncClient", _RecordingClient)
    return _RecordingClient


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Settings with feature flags that reset after each test.

    ``data_dir`` is redirected because searching consumes the daily budget,
    which is a file under it — tests must never spend the real allowance.
    """
    s = get_settings()
    original = dict(s._models_cfg.get("features", {}))
    original_key = s.tavily_api_key
    original_dir = s.data_dir
    s.data_dir = tmp_path
    s._models_cfg.setdefault("features", {})["web_search_daily_limit"] = 1000
    yield s
    s._models_cfg["features"] = original
    s.tavily_api_key = original_key
    s.data_dir = original_dir


def configure(settings, **overrides):
    settings._models_cfg.setdefault("features", {}).update(overrides)
    return settings


def test_disabled_makes_no_request(settings, http):
    configure(settings, web_search=False)
    assert asyncio.run(tools.search_recipes_web("q")) == []
    assert http.seen == {}, "issued a request while web search was disabled"


def test_tavily_without_key_is_skipped(settings, http):
    configure(settings, web_search=True, web_search_provider="tavily")
    settings.tavily_api_key = ""
    assert settings.web_search_ready is False
    assert asyncio.run(tools.search_recipes_web("q")) == []
    assert http.seen == {}, "called Tavily without an API key"


def test_tavily_posts_with_bearer_auth(settings, http):
    configure(settings, web_search=True, web_search_provider="tavily")
    settings.tavily_api_key = "tvly-test-key"
    assert settings.web_search_ready is True

    results = asyncio.run(tools.search_recipes_web("indian lunch recipe rice", limit=2))

    assert http.seen["method"] == "POST"
    assert http.seen["url"] == "https://api.tavily.com/search"
    assert http.seen["headers"]["Authorization"] == "Bearer tvly-test-key"
    assert http.seen["body"]["max_results"] == 2
    assert http.seen["body"]["search_depth"] == "basic"
    assert http.seen["timeout"] == settings.web_search_timeout_s
    assert [r["title"] for r in results] == ["Chana Masala", "Rajma"]
    assert len(results[0]["snippet"]) == 300, "long snippets must be truncated"


def test_searxng_uses_get_and_sends_no_credentials(settings, http):
    configure(settings, web_search=True, web_search_provider="searxng")
    results = asyncio.run(tools.search_recipes_web("q"))

    assert http.seen["method"] == "GET"
    assert http.seen["params"]["format"] == "json"
    assert [r["title"] for r in results] == ["Searxng Dal"]


def test_unknown_provider_is_skipped(settings, http):
    configure(settings, web_search=True, web_search_provider="bing")
    assert asyncio.run(tools.search_recipes_web("q")) == []
    assert http.seen == {}


def test_provider_failure_falls_back_to_empty(settings, monkeypatch):
    configure(settings, web_search=True, web_search_provider="tavily")
    settings.tavily_api_key = "tvly-test-key"
    monkeypatch.setattr(tools.httpx, "AsyncClient", _FailingClient)
    # Must not raise: planning continues on the model's own knowledge.
    assert asyncio.run(tools.search_recipes_web("q")) == []


def test_query_is_built_from_slot_cuisine_and_pantry():
    from app.agents.planner_nodes import web_search_query

    state = {
        "slot": "lunch",
        "cuisine_priority": ["indian", "mexican"],
        "pantry_text": "- rice: 1000.0 g (grain)\n- lentils: 500.0 g (legume)",
    }
    assert web_search_query(state) == "indian lunch recipe rice lentils"


def test_empty_pantry_line_is_not_treated_as_an_ingredient():
    from app.agents.planner_nodes import web_search_query

    state = {"slot": "dinner", "cuisine_priority": [], "pantry_text": "(pantry is empty)"}
    assert web_search_query(state) == "dinner recipe"


def test_node_feeds_results_into_web_context(settings, http):
    from app.agents.planner_nodes import search_web

    configure(settings, web_search=True, web_search_provider="tavily")
    settings.tavily_api_key = "tvly-test-key"
    state = {
        "slot": "lunch",
        "cuisine_priority": ["indian"],
        "pantry_text": "- rice: 1000.0 g (grain)",
    }
    out = asyncio.run(search_web(state, {}))
    assert "Chana Masala" in out["web_context"]


def test_node_is_a_noop_when_disabled(settings, http):
    from app.agents.planner_nodes import search_web

    configure(settings, web_search=False)
    assert asyncio.run(search_web({"slot": "lunch"}, {})) == {}
    assert http.seen == {}
