#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[bootstrap] Root: $ROOT_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "[bootstrap] Created .env from .env.example"
  echo "[bootstrap] Edit .env and set OPENAI_API_KEY (and optional DEEPGRAM_API_KEY / ELEVENLABS_API_KEY)."
fi

PY_BIN=""
if [[ -x .venv-voice/bin/python ]]; then
  PY_BIN="$(pwd)/.venv-voice/bin/python"
else
  if command -v uv >/dev/null 2>&1; then
    echo "[bootstrap] Creating .venv-voice with uv (Python 3.13)"
    uv venv -p 3.13 .venv-voice
    PY_BIN="$(pwd)/.venv-voice/bin/python"
  elif command -v python3.13 >/dev/null 2>&1; then
    echo "[bootstrap] Creating .venv-voice with python3.13"
    python3.13 -m venv .venv-voice
    PY_BIN="$(pwd)/.venv-voice/bin/python"
  else
    echo "[bootstrap] ERROR: Python 3.13 is required for Pipecat voice mode."
    echo "[bootstrap] Install uv and rerun: pip install uv && ./scripts/bootstrap.sh"
    exit 1
  fi
fi

echo "[bootstrap] Using Python: $PY_BIN"
"$PY_BIN" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$PY_BIN" -m pip install -U pip
"$PY_BIN" -m pip install -e '.[voice,knowledge]'

if ! command -v npm >/dev/null 2>&1; then
  echo "[bootstrap] ERROR: npm is required for UI build (install Node.js 20+)."
  exit 1
fi

echo "[bootstrap] Installing UI dependencies"
npm --prefix ui-next ci

echo "[bootstrap] Building UI"
npm --prefix ui-next run build

echo "[bootstrap] Deploying static voice client"
npm --prefix ui-next run deploy:voice-client

echo "[bootstrap] Verifying Python compile"
"$PY_BIN" -m compileall -q gm_engine scripts gm.py

echo ""
echo "[bootstrap] Done. Next:"
echo "  1) edit .env"
echo "  2) run ./scripts/start_voice_ws.sh"
