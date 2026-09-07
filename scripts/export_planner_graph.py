#!/usr/bin/env python3
"""Export the meal-planner LangGraph as Mermaid (.mmd) and optionally PNG.

Usage (from repo root, with .venv active or via path):

    .venv/bin/python scripts/export_planner_graph.py
    .venv/bin/python scripts/export_planner_graph.py --out docs/planner_graph
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))


def main() -> None:
    parser = argparse.ArgumentParser(description="Export planner LangGraph diagrams")
    parser.add_argument(
        "--out",
        default=str(ROOT / "docs" / "planner_graph"),
        help="Output path prefix (writes .mmd and .png)",
    )
    parser.add_argument(
        "--no-png",
        action="store_true",
        help="Skip PNG render (Mermaid source only)",
    )
    args = parser.parse_args()

    from app.agents.planner_graph import planner_mermaid, planner_mermaid_png

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    mmd_path = out.with_suffix(".mmd")
    mermaid = planner_mermaid()
    mmd_path.write_text(mermaid, encoding="utf-8")
    print(f"Wrote {mmd_path}")

    if not args.no_png:
        png_path = out.with_suffix(".png")
        try:
            png_path.write_bytes(planner_mermaid_png())
            print(f"Wrote {png_path}")
        except Exception as e:
            print(f"PNG render failed ({e}). Mermaid source is still at {mmd_path}")
            sys.exit(2)


if __name__ == "__main__":
    main()
