#!/usr/bin/env bash
# Start backend + frontend for local development.
# Usage (from repo root):  ./scripts/dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  echo
  echo "Stopping…"
  [[ -n "${FRONTEND_PID}" ]] && kill "${FRONTEND_PID}" 2>/dev/null || true
  [[ -n "${BACKEND_PID}" ]] && kill "${BACKEND_PID}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [[ ! -x "$VENV/bin/uvicorn" ]]; then
  echo "Missing $VENV — create it first:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -e \"./backend[dev]\""
  exit 1
fi

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "Installing frontend deps…"
  (cd "$ROOT/frontend" && npm install)
fi

mkdir -p "$ROOT/backend/data" "$ROOT/backend/uploads"

echo "Backend  → http://127.0.0.1:8000"
(
  cd "$ROOT/backend"
  exec "$VENV/bin/uvicorn" app.main:app --reload --host 0.0.0.0 --port 8000
) &
BACKEND_PID=$!

echo "Frontend → http://127.0.0.1:5173"
(
  cd "$ROOT/frontend"
  exec npm run dev -- --host 0.0.0.0 --port 5173
) &
FRONTEND_PID=$!

echo
echo "Both running. Ctrl+C to stop."
wait
