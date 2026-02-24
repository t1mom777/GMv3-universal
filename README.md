# GMv3 Proprietary Universal Builder

This project builds proprietary binary distributions for:
- Linux
- Windows
- macOS

It is designed for distribution where users can install and run, but do not receive plain project source files.

## Important limits
Absolute prevention of reverse engineering/copying is not technically guaranteed for client-side software. This package is hardened (compiled binary + stripped source maps + proprietary license), not mathematically unbreakable.

## ChatGPT account fallback (no API key)
This app now supports an LLM fallback using a local ChatGPT-authenticated Codex session:
- Set `GM_LLM_PROVIDER=codex_chatgpt`
- Run `codex login` on the target machine
- If `OPENAI_API_KEY` is absent, the app can use `codex exec` for LLM turns

Notes:
- This fallback is for **LLM** turns.
- STT/TTS still need their own credentials/providers (e.g., Deepgram/ElevenLabs/OpenAI).

## Local build (current OS)
```bash
python -m pip install -U pip
python -m pip install -e '.[voice,knowledge]' pyinstaller
npm --prefix ui-next ci
npm --prefix ui-next run build
npm --prefix ui-next run deploy:voice-client
python scripts/build_proprietary_package.py --output proprietary-dist
```

Artifacts will be in `proprietary-dist/`.

## Universal build (GitHub Actions)
Workflow file:
- `.github/workflows/build-proprietary-universal.yml`

Run via GitHub Actions `workflow_dispatch`.
It produces OS-specific artifacts for Linux, Windows, macOS.

## GitHub release (one-click downloads)
Release workflow file:
- `.github/workflows/release-proprietary-universal.yml`

This workflow builds all OS packages and publishes one GitHub Release with:
- Linux/Windows/macOS archives attached as release assets
- `SHA256SUMS.txt`
- auto-generated release notes

Ways to trigger:
- Push a tag like `v3.0.0`
- Or run `workflow_dispatch` and provide `version` (for example `v3.0.0`)

## Publish this repo to GitHub
Non-interactive publisher script:
- `scripts/publish_to_github.sh`

Required environment variables:
- `GITHUB_TOKEN` (repo scope)
- `GITHUB_OWNER` (example: `your-user`)
- `GITHUB_REPO` (example: `GMv3-proprietary-universal`)

Optional:
- `GITHUB_PRIVATE=true|false` (default: `true`)

Run:
```bash
chmod +x scripts/publish_to_github.sh
GITHUB_TOKEN=... GITHUB_OWNER=... GITHUB_REPO=... ./scripts/publish_to_github.sh
```

## Deliverable contents
Each generated package includes:
- Compiled app binary (`app/GMv3Server` / `app/GMv3Server.exe`)
- `.env.example`
- Install/run scripts (`install.sh` / `run.sh`, plus Windows scripts)
- `LICENSE-PROPRIETARY.txt`
- `README.md`

## Runtime config
Required in `.env`:
- `OPENAI_API_KEY` OR ChatGPT fallback (`GM_LLM_PROVIDER=codex_chatgpt` + `codex login`)

Optional:
- `DEEPGRAM_API_KEY`
- `ELEVENLABS_API_KEY`
