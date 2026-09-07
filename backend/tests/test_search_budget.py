"""The daily search cap and the prewarm failure cooldown.

Both exist to stop background loops from draining a metered API key, so the
tests focus on the runaway cases rather than the happy path.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from app.agents import tools
from app.core.config import get_settings
from app.services import prewarm, search_budget

from .test_web_search import _RecordingClient, configure  # reuse the HTTP stub


@pytest.fixture
def budget(tmp_path, monkeypatch):
    """Point the counter file at a temp dir and reset the limit afterwards."""
    s = get_settings()
    original_dir = s.data_dir
    original = dict(s._models_cfg.get("features", {}))
    s.data_dir = tmp_path
    yield s
    s.data_dir = original_dir
    s._models_cfg["features"] = original


def test_consume_counts_up_to_the_limit(budget):
    configure(budget, web_search_daily_limit=3)
    assert [search_budget.try_consume() for _ in range(5)] == [True, True, True, False, False]
    assert search_budget.usage() == (3, 3)


def test_limit_of_zero_blocks_everything(budget):
    configure(budget, web_search_daily_limit=0)
    assert search_budget.try_consume() is False
    assert search_budget.usage() == (0, 0)


def test_count_survives_a_restart(budget):
    """A reload must not hand out a fresh quota — that is the runaway case."""
    configure(budget, web_search_daily_limit=2)
    assert search_budget.try_consume() is True
    # Nothing cached in memory: the next reader re-reads the file.
    assert search_budget.usage()[0] == 1
    assert search_budget.try_consume() is True
    assert search_budget.try_consume() is False


def test_yesterdays_count_does_not_carry_over(budget, monkeypatch):
    configure(budget, web_search_daily_limit=2)
    search_budget.try_consume()
    search_budget.try_consume()
    assert search_budget.try_consume() is False

    monkeypatch.setattr(search_budget, "_today", lambda: "2099-01-01")
    assert search_budget.try_consume() is True, "new day should reset the allowance"


def test_exhausted_budget_makes_no_http_request(budget, monkeypatch):
    """The cap must be enforced before dispatch, not inside a provider."""
    configure(
        budget,
        web_search=True,
        web_search_provider="tavily",
        web_search_daily_limit=1,
    )
    budget.tavily_api_key = "tvly-test-key"
    _RecordingClient.seen = {}
    monkeypatch.setattr(tools.httpx, "AsyncClient", _RecordingClient)

    assert asyncio.run(tools.search_recipes_web("q")) != []
    _RecordingClient.seen = {}

    assert asyncio.run(tools.search_recipes_web("q")) == []
    assert _RecordingClient.seen == {}, "called the API after the budget was spent"


def test_failed_slot_is_not_retried_during_cooldown(budget):
    configure(budget, prewarm_failure_cooldown_s=3600)
    prewarm._failures.clear()
    prewarm._failures["lunch"] = datetime.utcnow()
    assert prewarm._in_cooldown("lunch") is True


def test_cooldown_expires(budget):
    configure(budget, prewarm_failure_cooldown_s=60)
    prewarm._failures.clear()
    prewarm._failures["lunch"] = datetime.utcnow() - timedelta(seconds=120)
    assert prewarm._in_cooldown("lunch") is False
    assert "lunch" not in prewarm._failures, "expired entry should be dropped"


def test_unrelated_slot_is_unaffected(budget):
    configure(budget, prewarm_failure_cooldown_s=3600)
    prewarm._failures.clear()
    prewarm._failures["lunch"] = datetime.utcnow()
    assert prewarm._in_cooldown("dinner") is False
