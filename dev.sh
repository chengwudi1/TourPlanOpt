#!/usr/bin/env bash
# TourPlanOpt dev launcher: backend (uvicorn, port 8000) + frontend (vite, port 5173).
# Usage: bash dev.sh          (Ctrl+C stops both)
#
# First run only:
#   cd backend && uv sync     # python deps
#   cd frontend && npm install
#   cp backend/.env.example backend/.env   then fill the two Amap keys (see .env)
#
# This file must stay LF: .gitattributes forces eol=lf for *.sh; with CRLF, bash dies
# with $'\r': command not found.
set -euo pipefail
cd "$(dirname "$0")"

cleanup() {
  # Kill the whole process group of the backend if it is still around.
  if [[ -n "${BACKEND_PID:-}" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "==> backend  http://127.0.0.1:8000"
(cd backend && uv run uvicorn app.main:app --reload --port 8000) &
BACKEND_PID=$!

echo "==> frontend http://127.0.0.1:5173  (API/WS proxied to :8000)"
(cd frontend && npm run dev)

wait "$BACKEND_PID" 2>/dev/null || true
