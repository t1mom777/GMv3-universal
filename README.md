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
- 👥 Multi-player voice detection (speaker diarization / voiceprint-ready flow, up to 8 players).
- 🌍 Automatic language handling for multilingual sessions.
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
1. Open the latest GitHub Release and download the archive for your OS.
2. Extract the archive.
3. Create `.env` from `.env.example`, then set your provider keys.

Linux:
```bash
./install.sh
# or portable run from extracted folder:
./run.sh
```

macOS:
```bash
chmod +x install.sh run.sh
./install.sh
# or portable:
./run.sh
```

Windows (PowerShell):
```powershell
.\install.ps1
# or portable run from extracted folder:
.\run.bat
```

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
