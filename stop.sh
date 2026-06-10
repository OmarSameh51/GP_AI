#!/usr/bin/env bash
# Stop the GP_AI FastAPI service (leaves Ollama running, since other apps may use it).
set -euo pipefail

pkill -f "uvicorn app.main:app" && echo "GP_AI stopped." || echo "GP_AI was not running."
