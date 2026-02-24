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
- **ChatGPT Codex mode**: set `GM_LLM_PROVIDER=codex_chatgpt` and run `codex login`

Notes:
- OpenAI and Codex ChatGPT are alternatives, not backup-only behavior.
- STT/TTS still use their own provider credentials (Deepgram / ElevenLabs / OpenAI).

## Install (Linux / Windows / macOS)
Download from the latest release:
`https://github.com/t1mom777/GMv3-universal/releases/latest`

All packaged installers are **guided first-run scripts**: they install files, create `.env`, prompt for auth mode, and print final localhost launch instructions.

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

Windows (x64, PowerShell):
1. Download `GMv3Pro-windows-x64-<version>.zip` from the release page.
2. Extract and enter folder:
```powershell
Expand-Archive -Path .\GMv3Pro-windows-x64-<version>.zip -DestinationPath .
Set-Location .\GMv3Pro-windows-x64-<version>
```
3. Create env file and add keys:
```powershell
Copy-Item .env.example .env
notepad .env
```
4. Run guided setup:
```powershell
.\install.ps1
```
5. Follow prompts (auth mode, `.env` editing, optional immediate launch).
6. Open the URL shown in terminal (usually `http://localhost:8000`).
7. Next launches can use:
```powershell
.\run.bat
```

Windows (x64, File Explorer click-through):
1. Download `GMv3Pro-windows-x64-<version>.zip`.
2. Right-click ZIP -> **Extract All...**.
3. Open extracted folder `GMv3Pro-windows-x64-<version>`.
4. Copy `.env.example` to `.env`, then edit `.env` in Notepad.
5. Right-click `install.ps1` -> **Run with PowerShell** and follow prompts.
6. Start with `run.bat`.
7. Open `http://localhost:8000` (or URL printed in terminal).

## 🤝 Need Help?
Feel free to contact me through GitHub Issues if you want help installing and setting up the app for your campaign. I can install and set it up for you, and help you get from download to first playable session.

## Deliverable contents
Each generated package includes:
- Compiled app binary (`app/GMv3Server` / `app/GMv3Server.exe`)
- `.env.example`
- Install/run scripts (`install.sh` / `run.sh`, plus Windows scripts)
- `LICENSE`
- `README.md`

## Runtime config
Required in `.env`:
- Choose one LLM auth mode:
  - OpenAI API: `GM_LLM_PROVIDER=openai` + `OPENAI_API_KEY=...`
  - ChatGPT Codex: `GM_LLM_PROVIDER=codex_chatgpt` + `codex login`

Optional:
- `DEEPGRAM_API_KEY`
- `ELEVENLABS_API_KEY`
