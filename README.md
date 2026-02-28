# GMv3 Universal Game Master Builder

🎲 Turn any tabletop rulebook PDF into a live AI Game Master experience.

Players can:
- 📚 upload any rulebook PDF and start a campaign quickly;
- 🎙️ play by voice with multilingual responses;
- 🧠 keep persistent campaign memory and player context across sessions.

This repository builds universal binary distributions for:
- Linux
- Windows
- macOS

## Why users download this
- 🚀 One setup flow: upload rulebook, choose voices/providers, play.
- 🗣️ Real-time GM voice + chat with interruption handling.
- 🎧 VAD-driven hands-free turns with natural barge-in interruption.
- 👥 Automatic player switching while people speak (voice-based speaker recognition with diarization + voiceprint flow, up to 8 players).
- 🌍 Automatic language switching in live play: the GM detects speaker language and responds in that language.
- 🧠 Large persistent memory layers: per-player history/skills/context, per-turn logs, per-campaign state, and world continuity.
- 💻 Works across Linux, Windows, and macOS with release installers.
- ⚙️ Tech stack: **RLM + LLM + Qdrant** with real-time voice.
- 🔌 Voice/AI options: **OpenAI**, **Deepgram**, **ElevenLabs (11Labs)**.

## LLM Authentication Options (Alternative Modes)
For **LLM turns**, you can use either mode:
- **OpenAI API mode**: set `GM_LLM_PROVIDER=openai` and `OPENAI_API_KEY=...`
- **ChatGPT Codex mode**: set `GM_LLM_PROVIDER=codex_chatgpt` and run bundled `codex login`

Notes:
- OpenAI and Codex ChatGPT are alternatives, not backup-only behavior.
- STT/TTS still use their own provider credentials (Deepgram / ElevenLabs / OpenAI).
- Codex CLI is bundled in release packages; no extra Codex download is required during install.

## Install (Linux / Windows / macOS)
Download from the latest release:
`https://github.com/t1mom777/GMv3-universal/releases/latest`

Release distributions are also mirrored in git under the `release-distributions` branch (folder per version tag).

All packaged installers are **guided first-run scripts**: they install files, create `.env`, prompt for auth mode, and print final localhost launch instructions.
Packages are self-contained (app runtime + dependencies + bundled Codex CLI), so installation itself does not download extra frameworks.

Linux (x64):
1. Download `GMv3Pro-linux-x64-<version>.tar.gz` (or `.zip`) from the release page.
2. Extract it:
```bash
tar -xzf GMv3Pro-linux-x64-<version>.tar.gz
# or:
unzip GMv3Pro-linux-x64-<version>.zip
```
3. Enter the extracted folder:
```bash
cd GMv3Pro-linux-x64-<version>
```
4. Create env file and add keys:
```bash
cp .env.example .env
```
5. Run guided setup:
```bash
./install.sh
```
6. Follow prompts (auth mode, `.env` editing, optional immediate launch).
7. Open the URL shown in terminal (usually `http://localhost:8000`).
8. Next launches can use:
```bash
./run.sh
```

macOS (Apple Silicon / arm64):
1. Download `GMv3Pro-macos-arm64-<version>.tar.gz` (or `.zip`) from the release page.
2. Extract it:
```bash
tar -xzf GMv3Pro-macos-arm64-<version>.tar.gz
# or:
unzip GMv3Pro-macos-arm64-<version>.zip
```
3. Enter the extracted folder:
```bash
cd GMv3Pro-macos-arm64-<version>
```
4. Create env file and add keys:
```bash
cp .env.example .env
```
5. Run guided setup:
```bash
chmod +x install.sh run.sh
./install.sh
```
6. Follow prompts (auth mode, `.env` editing, optional immediate launch).
7. Open the URL shown in terminal (usually `http://localhost:8000`).
8. Next launches can use:
```bash
./run.sh
```

Windows (x64, GUI / File Explorer):
1. Download `GMv3Pro-windows-x64-<version>.zip`.
2. Right-click ZIP -> **Extract All...**.
3. Open extracted folder `GMv3Pro-windows-x64-<version>`.
4. Copy `.env.example` to `.env`, then edit `.env` in Notepad.
5. Double-click `START_GMv3Pro.bat` (easy-to-find main launcher).
6. First launch runs a guided, colorful terminal setup (ASCII banner + auth mode + `.env` editing + optional ChatGPT login).
7. Use the same `START_GMv3Pro.bat` menu for run/setup/login/env changes.
8. Open `http://localhost:8000` (or URL printed in terminal).

## 🤝 Need Help?
Feel free to contact me through GitHub Issues if you want help installing and setting up the app for your campaign. I can install and set it up for you, and help you get from download to first playable session.

## Deliverable contents
Each generated package includes:
- Compiled app binary (`app/GMv3Server` / `app/GMv3Server.exe`)
- Bundled Codex CLI native binary (`app/codex/...`) + bundled `rg` helper (`app/path/...`)
- `.env.example`
- Install/run scripts (`install.sh` / `run.sh`, plus easy-to-find Windows launcher `START_GMv3Pro.bat`)
- `LICENSE`
- `README.md`

## Runtime config
Required in `.env`:
- Choose one LLM auth mode:
  - OpenAI API: `GM_LLM_PROVIDER=openai` + `OPENAI_API_KEY=...`
  - ChatGPT Codex: `GM_LLM_PROVIDER=codex_chatgpt` + bundled `codex login`

Optional:
- `DEEPGRAM_API_KEY`
- `ELEVENLABS_API_KEY`
