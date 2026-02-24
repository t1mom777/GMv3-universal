#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x "$ROOT_DIR/.venv-voice/bin/python" ]]; then
  PY_BIN="$ROOT_DIR/.venv-voice/bin/python"
elif [[ -x "$ROOT_DIR/../.venv-voice/bin/python" ]]; then
  PY_BIN="$ROOT_DIR/../.venv-voice/bin/python"
else
  echo "[build] ERROR: .venv-voice not found."
  exit 1
fi

echo "[build] Using Python: $PY_BIN"
"$PY_BIN" -m pip install -q -U pyinstaller

STAMP="$(date +%Y%m%d-%H%M%S)"
WORK_DIR="$ROOT_DIR/build/proprietary-${STAMP}"
mkdir -p "$WORK_DIR"

"$PY_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name GMv3Server \
  --distpath "$WORK_DIR/dist" \
  --workpath "$WORK_DIR/work" \
  --specpath "$WORK_DIR/spec" \
  --paths "$ROOT_DIR" \
  --add-data "$ROOT_DIR/docs/voice_client:docs/voice_client" \
  --add-data "$ROOT_DIR/.env.example:." \
  --add-data "$ROOT_DIR/gm_engine/prompts/gm_prompts.json:gm_engine/prompts" \
  --hidden-import gm_engine.interaction.control_processor \
  --hidden-import gm_engine.interaction.pipecat_rlm_processor \
  --hidden-import gm_engine.interaction.pipecat_ws_serializer \
  --hidden-import gm_engine.interaction.bot_speaking_state \
  --hidden-import gm_engine.interaction.deepgram_stt \
  --hidden-import pipecat.services.openai.stt \
  --hidden-import pipecat.services.openai.tts \
  --hidden-import pipecat.services.deepgram.stt \
  --hidden-import pipecat.services.elevenlabs.tts \
  --hidden-import qdrant_client \
  scripts/proprietary_entry.py

PKG_ROOT="$ROOT_DIR/../GMv3-proprietary-v1"
PKG_DIR="$PKG_ROOT/GMv3Pro-linux-x64-${STAMP}"
mkdir -p "$PKG_DIR/app"
cp -a "$WORK_DIR/dist/GMv3Server/." "$PKG_DIR/app/"
cp -a "$ROOT_DIR/.env.example" "$PKG_DIR/.env.example"

# Remove frontend source maps from distributed bundle.
find "$PKG_DIR/app" -type f -name "*.map" -delete

cat > "$PKG_DIR/run.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from template. Fill API keys and rerun."
fi
exec "$ROOT_DIR/app/GMv3Server" --mode voice-ws
SH
chmod +x "$PKG_DIR/run.sh"

cat > "$PKG_DIR/install.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${HOME}/.local/GMv3Pro"
mkdir -p "$TARGET_DIR"
cp -a "$SRC_DIR/app" "$TARGET_DIR/"
cp -a "$SRC_DIR/.env.example" "$TARGET_DIR/"
cp -a "$SRC_DIR/run.sh" "$TARGET_DIR/"
chmod +x "$TARGET_DIR/run.sh"
cat <<MSG
Installed to: $TARGET_DIR
Next:
  cd $TARGET_DIR
  cp .env.example .env
  # set API keys in .env
  ./run.sh
MSG
SH
chmod +x "$PKG_DIR/install.sh"

cat > "$PKG_DIR/README.md" <<'MD'
# GMv3 Pro Binary (Linux x64)

This package is a compiled proprietary distribution.

## Install
```bash
./install.sh
```

## Run (without install)
```bash
cp .env.example .env
# edit .env and set API keys
./run.sh
```

## Run (after install)
```bash
cd ~/.local/GMv3Pro
cp .env.example .env
# edit .env and set API keys
./run.sh
```

## Required env
- `OPENAI_API_KEY`

Optional:
- `DEEPGRAM_API_KEY`
- `ELEVENLABS_API_KEY`

## Notes
- This build is hardened and ships no plain project source.
- Absolute prevention of reverse engineering is not technically guaranteed.
MD

cat > "$PKG_DIR/LICENSE-PROPRIETARY.txt" <<'TXT'
Copyright (c) 2026.
All rights reserved.

This software is licensed, not sold.
You may install and use one copy for internal use.
You may not modify, redistribute, sublicense, reverse engineer,
decompile, disassemble, or create derivative works except where
applicable law explicitly permits despite this restriction.
TXT

( cd "$PKG_ROOT" && tar -czf "$(basename "$PKG_DIR").tar.gz" "$(basename "$PKG_DIR")" )

echo "[build] Done"
echo "[build] Package directory: $PKG_DIR"
echo "[build] Archive: $PKG_ROOT/$(basename "$PKG_DIR").tar.gz"
