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

## ChatGPT account fallback (no API key)
This app now supports an LLM fallback using a local ChatGPT-authenticated Codex session:
- Set `GM_LLM_PROVIDER=codex_chatgpt`
- Run `codex login` on the target machine
- If `OPENAI_API_KEY` is absent, the app can use `codex exec` for LLM turns

Notes:
- This fallback is for **LLM** turns.
- STT/TTS still need their own credentials/providers (e.g., Deepgram/ElevenLabs/OpenAI).

## Install (Linux / Windows / macOS)
Download from the latest release:
`https://github.com/t1mom777/GMv3-universal/releases/latest`

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
5. Run setup/start:
```bash
./install.sh
# next launches can use:
./run.sh
```
6. Open the URL shown in terminal (usually `http://localhost:8000`).

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
5. Run setup/start:
```bash
chmod +x install.sh run.sh
./install.sh
# next launches can use:
./run.sh
```
6. Open the URL shown in terminal (usually `http://localhost:8000`).

## Windows (x64) — Installation via File Explorer

---

### 1. Download the Release
1. Go to the project's **Releases** page on GitHub.
2. Download:  
   `GMv3Pro-windows-x64-<version>.zip`
---

### 2. Extract the ZIP File
1. Open **File Explorer**.
2. Navigate to your downloaded `.zip` file.
3. Right-click the file → select **Extract All…**
4. Choose a destination folder (or keep the default).
5. Click **Extract**.

This will create a new folder named:

GMv3Pro-windows-x64-<version>

---

### 3. Create the `.env` File
1. Open the extracted folder.
2. Locate the file:

.env.example

3. Right-click → **Copy**
4. Right-click empty space in the folder → **Paste**
5. Rename the copied file to:

.env

> If Windows warns about changing the file extension, click **Yes**.

6. Right-click `.env` → **Open with → Notepad**
7. Add your required API keys and configuration values.
8. Click **File → Save**, then close Notepad.

---

### 4. Run Initial Setup

1. In the same folder, locate:

install.ps1

2. Right-click → **Run with PowerShell**
3. If prompted for permission, allow it.
4. Wait until the installation completes.

---

### 5. Start the Application

After installation:
1. Double-click:

run.bat

A terminal window will open and display a local URL.

---

### 6. Open in Browser
Open your browser and navigate to:

http://localhost:8000

(If a different URL is shown in the terminal, use that instead.)

---

## ✅ Done

The application should now be running locally on your Windows machine.
```
5. Open the URL shown in terminal (usually `http://localhost:8000`).

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
- `OPENAI_API_KEY` OR ChatGPT fallback (`GM_LLM_PROVIDER=codex_chatgpt` + `codex login`)

Optional:
- `DEEPGRAM_API_KEY`
- `ELEVENLABS_API_KEY`
