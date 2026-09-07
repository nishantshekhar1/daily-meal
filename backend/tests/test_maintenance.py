"""Background maintenance and cache invalidation.

The cache test here guards the subtlest failure in the whole loop: feedback
recorded correctly, aggregated correctly, and then never reaching the user
because a stale prewarmed plan keeps being served.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress

from app.models import FeedbackSignal
from app.services import feedback as fb
from app.services import maintenance, metrics, prewarm
from app.services import preference as pref

# ── Cache invalidation ───────────────────────────────────────────────────────


def test_fingerprint_changes_when_preferences_change(db, make_meal):
    """Otherwise learned preference never reaches the user.

    The prewarm cache is keyed on everything that changes a plan's outcome.
    Learned preference feeds both ranking and the shortlist prompt, so it has
    to be in the key or a plan generated before any feedback would be served
    indefinitely.
    """
    _, _, planned = make_meal()
    before = prewarm.fingerprint(db)

    fb.record(db, planned.id, FeedbackSignal.thumbs_up)
    pref.rebuild_profiles(db)
    after = prewarm.fingerprint(db)

    assert before != after


def test_fingerprint_is_stable_when_nothing_changes(db, make_meal):
    """Churning the key on every call would defeat prewarming entirely."""
    make_meal()
    assert prewarm.fingerprint(db) == prewarm.fingerprint(db)


def test_cached_plan_is_dropped_after_feedback(db, make_meal):
    _, _, planned = make_meal()
    prewarm.write_cache(db, "dinner", {"planned_meals": [{"dish_name": "Dal"}]})
    assert prewarm.read_cache(db, "dinner") is not None

    fb.record(db, planned.id, FeedbackSignal.thumbs_down)
    pref.rebuild_profiles(db)

    assert prewarm.read_cache(db, "dinner") is None


# ── Maintenance pass ─────────────────────────────────────────────────────────


def test_run_once_sweeps_aggregates_and_measures(db, make_meal, aged_plan):
    make_meal(plan=aged_plan(hours_ago=48), name="Forgotten")

    summary = maintenance.run_once(db)

    assert summary["swept"] == 1
    assert summary["profiles"] > 0
    assert summary["suggestions_measured"] == 1
    assert metrics.latest(db) is not None


def test_run_once_on_an_empty_app_is_harmless(db):
    summary = maintenance.run_once(db)

    assert summary == {"swept": 0, "profiles": 0, "suggestions_measured": 0}
    assert metrics.latest(db) is not None


def test_run_once_is_idempotent(db, make_meal, aged_plan):
    make_meal(plan=aged_plan(hours_ago=48))

    first = maintenance.run_once(db)
    second = maintenance.run_once(db)

    assert first["swept"] == 1
    assert second["swept"] == 0
    assert second["profiles"] == first["profiles"]


def test_sweep_feeds_the_preference_model(db, make_meal, aged_plan):
    """A meal nobody cooked should end up as mild negative evidence."""
    make_meal(plan=aged_plan(hours_ago=48), cuisine="norwegian")

    maintenance.run_once(db)

    from app.models import PreferenceScope

    assert pref.load_scores(db, PreferenceScope.cuisine)["norwegian"] < 0


# ── Worker loop ──────────────────────────────────────────────────────────────


def test_loop_runs_then_stops_cleanly(monkeypatch):
    calls = []
    monkeypatch.setattr(maintenance, "run_once", lambda _db: calls.append(1) or {
        "swept": 0, "profiles": 0, "suggestions_measured": 0
    })
    monkeypatch.setattr(
        maintenance.get_settings(), "_models_cfg",
        {"features": {"feedback_maintenance_interval_s": 300}},
        raising=False,
    )

    async def drive():
        stop = asyncio.Event()
        task = asyncio.create_task(maintenance.maintenance_loop(stop))
        await asyncio.sleep(0.05)
        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(drive())
    assert calls, "loop should have run at least once"


def test_a_failing_run_does_not_kill_the_worker(monkeypatch):
    """One bad pass must not silently stop all future maintenance."""
    calls = []

    def flaky(_db):
        calls.append(1)
        raise RuntimeError("transient")

    monkeypatch.setattr(maintenance, "run_once", flaky)

    async def drive():
        stop = asyncio.Event()
        task = asyncio.create_task(maintenance.maintenance_loop(stop))
        await asyncio.sleep(0.05)
        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(drive())
    assert calls, "worker should have attempted a run and survived the error"
