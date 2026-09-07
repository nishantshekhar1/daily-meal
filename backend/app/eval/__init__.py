"""Offline evaluation of suggestion quality.

Separate from ``tests/``: those assert that code behaves, these assert that the
*model* behaves. They need a running Ollama and take minutes, so they are a CLI
you point at a box rather than something CI runs.

The harness exists mainly to answer a question the VRAM profiles created and
could not otherwise settle: does the 12 GB profile actually produce worse plans
than the 24 GB one, and by how much? Every check here is structural and needs
no human judgement, so the answer is a number rather than an opinion.
"""
from app.eval.checks import CheckResult, run_checks
from app.eval.scenarios import SCENARIOS, Scenario

__all__ = ["CheckResult", "run_checks", "SCENARIOS", "Scenario"]
