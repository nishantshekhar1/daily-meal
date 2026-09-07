"""Run evaluation scenarios through the real planner and score the results.

Usage:

    # Evaluate whatever CONFIG_PATH currently points at
    python -m app.eval.runner

    # Compare model profiles against each other
    python -m app.eval.runner --profiles ../config/models.yaml \\
        ../config/profiles/16gb.yaml ../config/profiles/12gb.yaml

Each scenario gets a throwaway in-memory database seeded with the production
ingredient vocabulary, so nothing here touches the real pantry.

Profiles are compared by re-executing this module in a subprocess per profile
rather than by swapping settings in-process: ``get_settings`` and the LLM
client are both cached, and a stale cache would silently evaluate the wrong
model — the exact failure this harness exists to catch.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.models  # noqa: F401  (populates SQLModel.metadata)
from app.db.seed import seed_into
from app.eval.checks import CheckResult, run_checks
from app.eval.scenarios import SCENARIOS, Scenario, by_name
from app.models import CanonicalIngredient, HouseholdMember, MealSlot, StockLot


@dataclass
class ScenarioReport:
    scenario: str
    passed: bool
    elapsed_s: float
    dish_count: int
    checks: list[CheckResult] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


@dataclass
class SuiteReport:
    profile: str
    model: str
    started_at: str
    scenarios: list[ScenarioReport] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for s in self.scenarios if s.passed)

    @property
    def total(self) -> int:
        return len(self.scenarios)

    @property
    def total_elapsed_s(self) -> float:
        return round(sum(s.elapsed_s for s in self.scenarios), 1)


# ── Fixture database ─────────────────────────────────────────────────────────


def build_scenario_db(scenario: Scenario) -> Session:
    """A throwaway database holding exactly this scenario's household and pantry."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)

    seed_into(session)

    for member in scenario.members:
        session.add(
            HouseholdMember(
                name=member.name,
                kind=member.kind,
                age_months=member.age_months,
                active=True,
            )
        )

    for item in scenario.stock:
        ingredient = session.exec(
            select(CanonicalIngredient).where(CanonicalIngredient.name == item.ingredient)
        ).first()
        if ingredient is None:
            raise KeyError(
                f"Scenario '{scenario.name}' references unseeded ingredient "
                f"'{item.ingredient}'"
            )
        session.add(
            StockLot(
                ingredient_id=ingredient.id,
                quantity=item.quantity,
                original_quantity=item.quantity,
                unit=item.unit,
                source="manual",
                acquired_at=datetime.utcnow(),
            )
        )

    session.commit()
    return session


# ── Execution ────────────────────────────────────────────────────────────────


async def run_scenario(scenario: Scenario, llm: Any = None) -> ScenarioReport:
    """Plan one scenario and score the result."""
    from app.agents.planner import MealPlannerAgent
    from app.services.llm_client import get_llm_client

    db = build_scenario_db(scenario)
    started = time.monotonic()
    error: Optional[str] = None
    result: dict[str, Any] = {}

    try:
        agent = MealPlannerAgent(db, llm or get_llm_client())
        members = list(db.exec(select(HouseholdMember)).all())
        result = await agent.suggest(slot=MealSlot(scenario.slot), members=members)
    except Exception as e:
        # A crash is a legitimate result to record, not a reason to abort the
        # suite — it is exactly what the smallest profiles are expected to do.
        error = f"{type(e).__name__}: {e}"
        result = {"error": error}

    elapsed = round(time.monotonic() - started, 1)
    checks = run_checks(result, scenario, db)
    db.close()

    return ScenarioReport(
        scenario=scenario.name,
        passed=all(c.passed for c in checks),
        elapsed_s=elapsed,
        dish_count=len(result.get("planned_meals") or []),
        checks=checks,
        error=error,
    )


async def run_suite(
    scenarios: Optional[list[Scenario]] = None, llm: Any = None
) -> SuiteReport:
    from app.core.config import get_settings

    settings = get_settings()
    report = SuiteReport(
        profile=str(settings.config_path),
        model=str(settings.reasoning_cfg.get("model", "unknown")),
        started_at=datetime.utcnow().isoformat(timespec="seconds"),
    )
    for scenario in scenarios or SCENARIOS:
        print(f"  running {scenario.name}…", file=sys.stderr, flush=True)
        report.scenarios.append(await run_scenario(scenario, llm=llm))
    return report


# ── Reporting ────────────────────────────────────────────────────────────────


def format_report(report: SuiteReport) -> str:
    lines = [
        "",
        f"Profile : {report.profile}",
        f"Model   : {report.model}",
        f"Result  : {report.passed}/{report.total} scenarios clean "
        f"in {report.total_elapsed_s}s",
        "",
        f"{'scenario':<20} {'dishes':>6} {'time':>7}  checks",
        "-" * 72,
    ]
    for s in report.scenarios:
        summary = "all passed" if s.passed else ", ".join(c.name for c in s.failures)
        lines.append(f"{s.scenario:<20} {s.dish_count:>6} {s.elapsed_s:>6}s  {summary}")

    detail = [s for s in report.scenarios if not s.passed]
    if detail:
        lines += ["", "Failures", "-" * 72]
        for s in detail:
            lines.append(f"{s.scenario}:")
            for c in s.failures:
                lines.append(f"    {c.name}: {c.detail or 'failed'}")
    lines.append("")
    return "\n".join(lines)


def format_comparison(reports: list[SuiteReport]) -> str:
    """Side-by-side profile comparison — the table the VRAM profiles needed."""
    if not reports:
        return "no reports"

    scenario_names = [s.scenario for s in reports[0].scenarios]
    width = max(len(r.model) for r in reports) + 2

    lines = ["", "Profile comparison", "=" * 72, ""]
    header = f"{'scenario':<20}" + "".join(f"{r.model:<{width}}" for r in reports)
    lines += [header, "-" * len(header)]

    for name in scenario_names:
        row = f"{name:<20}"
        for report in reports:
            match = next((s for s in report.scenarios if s.scenario == name), None)
            if match is None:
                cell = "-"
            elif match.passed:
                cell = f"pass {match.elapsed_s}s"
            else:
                cell = f"FAIL({len(match.failures)}) {match.elapsed_s}s"
            row += f"{cell:<{width}}"
        lines.append(row)

    lines += ["-" * len(header)]
    totals = f"{'passed':<20}"
    for report in reports:
        totals += f"{f'{report.passed}/{report.total}':<{width}}"
    lines += [totals, ""]
    return "\n".join(lines)


def _serialize(report: SuiteReport) -> dict:
    payload = asdict(report)
    payload["passed"] = report.passed
    payload["total"] = report.total
    payload["total_elapsed_s"] = report.total_elapsed_s
    return payload


# ── CLI ──────────────────────────────────────────────────────────────────────


def _run_profile_subprocess(profile: str, scenarios: list[str]) -> Optional[SuiteReport]:
    """Evaluate one profile in a clean interpreter so no cached settings leak."""
    env = {**os.environ, "CONFIG_PATH": profile}
    cmd = [sys.executable, "-m", "app.eval.runner", "--json"]
    for name in scenarios:
        cmd += ["--scenario", name]

    print(f"\n=== {profile} ===", file=sys.stderr, flush=True)
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        print(f"profile {profile} exited {proc.returncode}", file=sys.stderr)
        return None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"profile {profile} produced no JSON report", file=sys.stderr)
        return None

    return SuiteReport(
        profile=payload["profile"],
        model=payload["model"],
        started_at=payload["started_at"],
        scenarios=[
            ScenarioReport(
                scenario=s["scenario"],
                passed=s["passed"],
                elapsed_s=s["elapsed_s"],
                dish_count=s["dish_count"],
                checks=[CheckResult(**c) for c in s["checks"]],
                error=s.get("error"),
            )
            for s in payload["scenarios"]
        ],
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="Run only this scenario (repeatable). Default: all.",
    )
    parser.add_argument(
        "--profiles",
        nargs="+",
        help="Compare these config files, one subprocess each.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    parser.add_argument("--out", help="Also write the JSON report to this path.")
    args = parser.parse_args(argv)

    selected = [by_name(n) for n in args.scenario] if args.scenario else SCENARIOS

    if args.profiles:
        reports = [
            r
            for r in (
                _run_profile_subprocess(p, [s.name for s in selected])
                for p in args.profiles
            )
            if r is not None
        ]
        print(format_comparison(reports))
        if args.out:
            with open(args.out, "w") as f:
                json.dump([_serialize(r) for r in reports], f, indent=2)
            print(f"Wrote {args.out}")
        return 0 if reports and all(r.passed == r.total for r in reports) else 1

    report = asyncio.run(run_suite(selected))

    if args.json:
        print(json.dumps(_serialize(report), indent=2))
    else:
        print(format_report(report))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(_serialize(report), f, indent=2)

    return 0 if report.passed == report.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
