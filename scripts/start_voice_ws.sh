#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x .venv-voice/bin/python ]]; then
  echo "[start] ERROR: .venv-voice is missing. Run ./scripts/bootstrap.sh first."
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "[start] Created .env from template. Fill API keys before continuing."
fi

export GM_KILL_CONFLICTING_PORTS="${GM_KILL_CONFLICTING_PORTS:-1}"

exec .venv-voice/bin/python gm.py --mode voice-ws
