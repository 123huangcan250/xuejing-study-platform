#!/usr/bin/env bash
# 一键启动（macOS / Linux / Git Bash）。后端 :8400，前端 :3400
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
if [ ! -d .venv ]; then
  if command -v uv >/dev/null; then uv venv .venv --python 3.11 && uv pip install -p .venv -r requirements.txt
  else python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; fi
fi
PY=.venv/bin/python; [ -f .venv/Scripts/python.exe ] && PY=.venv/Scripts/python.exe
$PY -m uvicorn app.main:app --host 127.0.0.1 --port 8400 --reload &
BE=$!
trap 'kill $BE 2>/dev/null' EXIT
cd "$ROOT/frontend"
[ -d node_modules ] || npm install
echo "打开 http://localhost:3400"
npm run dev
