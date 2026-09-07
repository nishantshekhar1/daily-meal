#!/usr/bin/env python3
"""Pull the Ollama models required by Daily Meal Planner.

Requires `ollama` on PATH and a running Ollama service (`ollama serve`).

Usage:
    python scripts/download_models.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys

# Keep in sync with config/models.yaml
MODELS = [
    {
        "name": "qwen3.6:35b",
        "description": "Reasoning / meal-planning agent (tool calling)",
        "required": True,
    },
    {
        "name": "qwen3-vl:8b",
        "description": "Vision + receipt OCR",
        "required": True,
    },
    {
        "name": "nomic-embed-text",
        "description": "Embeddings for ingredient canonicalization",
        "required": True,
    },
]


def _listed_names() -> set[str]:
    result = subprocess.run(
        ["ollama", "list"],
        check=True,
        capture_output=True,
        text=True,
    )
    names: set[str] = set()
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if parts:
            names.add(parts[0])
    return names


def _have_model(have: set[str], name: str) -> bool:
    if name in have:
        return True
    # nomic-embed-text matches nomic-embed-text:latest
    if f"{name}:latest" in have:
        return True
    if ":" not in name:
        return any(h == name or h.startswith(name + ":") for h in have)
    return False


def pull_all() -> None:
    if shutil.which("ollama") is None:
        print("ollama not found on PATH. Install from https://ollama.com/download")
        sys.exit(1)

    try:
        have = _listed_names()
    except subprocess.CalledProcessError as e:
        print("Cannot talk to Ollama. Is the Ollama service running?")
        print(e.stderr or e)
        sys.exit(1)

    for m in MODELS:
        name = m["name"]
        print(f"\n── {name}  ({m['description']}) ──")
        if _have_model(have, name):
            print("   ✓ already present")
            continue
        try:
            subprocess.run(["ollama", "pull", name], check=True)
            print(f"   ✓ pulled {name}")
            have = _listed_names()
        except subprocess.CalledProcessError as e:
            if m["required"]:
                print(f"   ✗ FAILED (required): {e}")
                sys.exit(1)
            print(f"   ! Failed (optional): {e}")


if __name__ == "__main__":
    pull_all()
    print("\nAll Ollama models ready.")
