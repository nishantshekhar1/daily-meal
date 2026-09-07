"""HTTP surface of the feedback loop.

Exercises the routes against a real (in-memory) database rather than mocking
the service layer, so the request schemas, dependency wiring and the
rebuild-on-rate side effect are all covered.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import app
from app.models import FeedbackSignal, PreferenceScope
from app.services import feedback as fb
from app.services import preference as pref


@pytest.fixture
def client(db):
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_rate_meal_records_and_rebuilds(client, db, make_meal):
    """One POST should both log the event and make the score readable."""
    _, _, planned = make_meal(cuisine="indian", ingredients=["chickpeas"])

    res = client.post(
        "/api/v1/feedback/meal",
        json={"planned_meal_id": planned.id, "signal": "thumbs_up"},
    )

    assert res.status_code == 200
    assert res.json()["signal"] == "thumbs_up"
    assert pref.load_scores(db, PreferenceScope.cuisine)["indian"] > 0


def test_rate_meal_accepts_a_reason(client, make_meal):
    _, _, planned = make_meal()

    res = client.post(
        "/api/v1/feedback/meal",
        json={
            "planned_meal_id": planned.id,
            "signal": "thumbs_down",
            "reason": "too_bland",
        },
    )

    assert res.status_code == 200
    assert res.json()["reason"] == "too_bland"


def test_implicit_signals_are_not_settable_over_http(client, make_meal):
    """Only a person can thumbs; 'cooked' is the backend's to infer."""
    _, _, planned = make_meal()

    res = client.post(
        "/api/v1/feedback/meal",
        json={"planned_meal_id": planned.id, "signal": "cooked"},
    )

    assert res.status_code == 422


def test_rate_unknown_meal_is_a_client_error(client):
    res = client.post(
        "/api/v1/feedback/meal",
        json={"planned_meal_id": 4242, "signal": "thumbs_up"},
    )
    assert res.status_code == 400


def test_plan_feedback_reports_current_ratings(client, db, make_meal):
    plan, _, first = make_meal(name="Dal")
    _, _, second = make_meal(name="Paneer", plan=plan)
    fb.record(db, first.id, FeedbackSignal.thumbs_up)

    res = client.get(f"/api/v1/feedback/plan/{plan.id}")

    assert res.status_code == 200
    body = res.json()
    assert body[str(first.id)]["signal"] == "thumbs_up"
    assert str(second.id) not in body


def test_preferences_endpoint_ranks_strongest_opinions_first(client, db, make_meal):
    _, _, loved = make_meal(name="A", cuisine="indian")
    _, _, meh = make_meal(name="B", cuisine="italian")
    for _ in range(1):
        fb.record(db, loved.id, FeedbackSignal.thumbs_up)
    fb.record_cooked(db, meh.id)
    pref.rebuild_profiles(db)

    res = client.get("/api/v1/feedback/preferences?scope=cuisine")

    assert res.status_code == 200
    rows = res.json()
    assert [r["scope_key"] for r in rows][0] == "indian"
    assert all(r["scope"] == "cuisine" for r in rows)


def test_preferences_rejects_unknown_scope(client):
    assert client.get("/api/v1/feedback/preferences?scope=nonsense").status_code == 400


def test_rebuild_endpoint_is_callable(client, db, make_meal):
    _, _, planned = make_meal()
    fb.record(db, planned.id, FeedbackSignal.thumbs_up)

    res = client.post("/api/v1/feedback/rebuild")

    assert res.status_code == 200
    assert res.json()["profiles"] > 0


def test_sweep_endpoint_reports_what_it_recorded(client, make_meal, aged_plan):
    make_meal(plan=aged_plan(hours_ago=48))

    res = client.post("/api/v1/feedback/sweep-skipped")

    assert res.status_code == 200
    assert res.json()["recorded"] == 1


def test_cooking_a_meal_records_implicit_feedback(client, db, make_meal, monkeypatch):
    """The cook route must log the signal without changing its own response."""
    _, _, planned = make_meal()

    monkeypatch.setattr(
        "app.api.routes.meals.cook_svc.cook_meal",
        lambda _db, meal_id: {"cook_event_id": 1, "deductions": [], "exhaustion_candidates": []},
    )
    res = client.post("/api/v1/meals/cook", json={"planned_meal_id": planned.id})

    assert res.status_code == 200
    assert res.json()["cook_event_id"] == 1
    rows = client.get(f"/api/v1/feedback/plan/{planned.plan_id}").json()
    # 'cooked' is implicit, so it must not surface as an explicit rating.
    assert rows == {}
    pref.rebuild_profiles(db)
    assert pref.load_scores(db, PreferenceScope.cuisine)["indian"] > 0


def test_failed_feedback_never_breaks_cooking(client, make_meal, monkeypatch):
    _, _, planned = make_meal()

    monkeypatch.setattr(
        "app.api.routes.meals.cook_svc.cook_meal",
        lambda _db, meal_id: {"cook_event_id": 7, "deductions": [], "exhaustion_candidates": []},
    )
    def boom(*_a, **_kw):
        raise RuntimeError("feedback backend exploded")
    monkeypatch.setattr("app.api.routes.meals.feedback_svc.record_cooked", boom)

    res = client.post("/api/v1/meals/cook", json={"planned_meal_id": planned.id})

    assert res.status_code == 200
    assert res.json()["cook_event_id"] == 7
