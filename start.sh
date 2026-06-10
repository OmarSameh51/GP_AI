#!/usr/bin/env bash
# Start the GP_AI stack: Ollama (desktop app) + the FastAPI service on :9100.
# Safe to re-run — skips anything already running.
set -euo pipefail

cd "$(dirname "$0")"

# 1) Make sure Ollama is up (the desktop app, not the broken Homebrew formula).
if ! curl -s -o /dev/null http://localhost:11434/api/tags; then
  echo "Starting Ollama..."
  open -a Ollama
  for i in $(seq 1 30); do
    if curl -s -o /dev/null http://localhost:11434/api/tags; then
      break
    fi
    sleep 1
  done
fi
echo "Ollama is up."

# 2) Start the FastAPI service if it's not already running.
if curl -s -o /dev/null http://localhost:9100/healthz; then
  echo "GP_AI already running on :9100"
else
  echo "Starting GP_AI on :9100..."
  nohup .venv/bin/uvicorn app.main:app --port 9100 > /tmp/gp_ai.log 2>&1 &
  disown
  for i in $(seq 1 30); do
    if curl -s -o /dev/null http://localhost:9100/healthz; then
      break
    fi
    sleep 1
  done
fi

curl -s http://localhost:9100/healthz && echo
echo "Logs: tail -f /tmp/gp_ai.log"
