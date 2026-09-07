"""Online quality metrics.

These are the numbers that tell you whether the feedback loop is working, so
the thing worth pinning down is that they stay honest at the edges: an unused
app, a window that excludes old data, and the difference between "nobody rated
anything" and "everybody hated it".
"""
from __future__ import annotations

from app.models import FeedbackSignal
from app.services import feedback as fb
from app.services import metrics


def test_empty_app_reports_zeroes_not_errors(db):
    """Division by zero everywhere; must read as 0, not blow up."""
    values = metrics.compute(db)

    assert values["suggestions_count"] == 0
    assert values["cook_through_rate"] == 0.0
    assert values["thumbs_up_rate"] == 0.0
    assert values["repetition_rate"] == 0.0


def test_cook_through_rate_counts_cooked_suggestions(db, make_meal):
    plan, _, _ = make_meal(name="A", cooked=True)
    make_meal(name="B", plan=plan, cooked=False)
    make_meal(name="C", plan=plan, cooked=False)
    make_meal(name="D", plan=plan, cooked=True)

    values = metrics.compute(db)

    assert values["suggestions_count"] == 4
    assert values["cooked_count"] == 2
    assert values["cook_through_rate"] == 0.5


def test_thumbs_up_rate_ignores_implicit_signals(db, make_meal):
    """Cooking is not a thumbs up; it must not inflate the explicit rate."""
    plan, _, a = make_meal(name="A")
    _, _, b = make_meal(name="B", plan=plan)
    _, _, c = make_meal(name="C", plan=plan)
    fb.record(db, a.id, FeedbackSignal.thumbs_up)
    fb.record(db, b.id, FeedbackSignal.thumbs_down)
    fb.record_cooked(db, c.id)

    values = metrics.compute(db)

    assert values["explicit_ratings"] == 2
    assert values["thumbs_up_rate"] == 0.5


def test_unrated_is_distinct_from_disliked(db, make_meal):
    """No ratings gives 0, which must not be read as 'everything was bad'."""
    make_meal()

    values = metrics.compute(db)

    assert values["explicit_ratings"] == 0
    assert values["thumbs_up_rate"] == 0.0


def test_repetition_rate_rises_when_variety_collapses(db, make_meal):
    """The metric that catches the feedback loop eating itself."""
    plan, dish, _ = make_meal(name="Same")
    from datetime import date

    from app.models import PlannedMeal

    for _ in range(3):
        db.add(
            PlannedMeal(
                plan_id=plan.id, dish_id=dish.id, day=date.today(), slot="dinner", servings=4
            )
        )
    db.commit()

    values = metrics.compute(db)

    assert values["suggestions_count"] == 4
    assert values["distinct_dishes"] == 1
    assert values["repetition_rate"] == 0.75


def test_full_variety_has_no_repetition(db, make_meal):
    plan, _, _ = make_meal(name="A")
    make_meal(name="B", plan=plan)
    make_meal(name="C", plan=plan)

    assert metrics.compute(db)["repetition_rate"] == 0.0


def test_reroll_rate_counts_plans_not_dishes(db, make_meal):
    """One reroll rejects a whole plan; that is one rejection, not three."""
    plan, _, _ = make_meal(name="A")
    make_meal(name="B", plan=plan)
    make_meal(name="C", plan=plan)
    fb.record_reroll(db, "dinner")

    values = metrics.compute(db)

    assert values["reroll_rate"] == 1.0


def test_window_excludes_older_activity(db, make_meal, aged_plan):
    make_meal(name="Old", plan=aged_plan(hours_ago=24 * 60), cooked=True)
    make_meal(name="New", cooked=False)

    wide = metrics.compute(db, window_days=365)
    narrow = metrics.compute(db, window_days=7)

    assert wide["suggestions_count"] == 2
    assert narrow["suggestions_count"] == 1
    assert narrow["cook_through_rate"] == 0.0


def test_snapshot_persists_and_reads_back(db, make_meal):
    make_meal(cooked=True)

    row = metrics.snapshot(db)

    assert row.id is not None
    assert row.cook_through_rate == 1.0
    assert metrics.latest(db).id == row.id
    assert len(metrics.history(db)) == 1


def test_history_is_newest_first(db, make_meal):
    make_meal()
    first = metrics.snapshot(db)
    second = metrics.snapshot(db)

    rows = metrics.history(db)

    assert [r.id for r in rows][:2] == [second.id, first.id]


def test_metrics_endpoints(client, make_meal):
    make_meal(cooked=True)

    current = client.get("/api/v1/feedback/metrics")
    assert current.status_code == 200
    assert current.json()["cook_through_rate"] == 1.0

    snap = client.post("/api/v1/feedback/metrics/snapshot")
    assert snap.status_code == 200

    hist = client.get("/api/v1/feedback/metrics/history")
    assert hist.status_code == 200
    assert len(hist.json()) == 1
