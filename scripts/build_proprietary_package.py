from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=str(cwd or ROOT), check=True)


def _python_bin() -> str:
    override = str(os.environ.get("GM_PYTHON_BIN") or "").strip()
    if override:
        return override
    candidates = [
        ROOT / ".venv-voice" / "bin" / "python",
        ROOT.parent / ".venv-voice" / "bin" / "python",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable


def _add_data_arg(src: Path, dst: str) -> str:
    sep = ";" if os.name == "nt" else ":"
    return f"{src}{sep}{dst}"


def _platform_tag() -> str:
    sys_name = platform.system().lower()
    machine = platform.machine().lower()
    arch = "x64"
    if machine in {"aarch64", "arm64"}:
        arch = "arm64"
    if sys_name.startswith("win"):
        return f"windows-{arch}"
    if sys_name == "darwin":
        return f"macos-{arch}"
    return f"linux-{arch}"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _remove_source_maps(root: Path) -> int:
    removed = 0
    for p in root.rglob("*.map"):
        try:
            p.unlink()
            removed += 1
        except Exception:
            pass
    return removed


def _copytree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _zip_dir(src_dir: Path, out_zip: Path) -> None:
    with ZipFile(out_zip, "w", compression=ZIP_DEFLATED, compresslevel=6) as zf:
        for p in src_dir.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src_dir.parent))


def _tar_dir(src_dir: Path, out_tgz: Path) -> None:
    with tarfile.open(out_tgz, "w:gz") as tf:
        tf.add(src_dir, arcname=src_dir.name)


def _build_pyinstaller(stamp: str) -> Path:
    build_root = ROOT / "build" / f"proprietary-{stamp}"
    dist_dir = build_root / "dist"
    work_dir = build_root / "work"
    spec_dir = build_root / "spec"
    build_root.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [
        _python_bin(),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "GMv3Server",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--paths",
        str(ROOT),
        "--add-data",
        _add_data_arg(ROOT / "docs" / "voice_client", "docs/voice_client"),
        "--add-data",
        _add_data_arg(ROOT / ".env.example", "."),
        "--add-data",
        _add_data_arg(ROOT / "gm_engine" / "prompts" / "gm_prompts.json", "gm_engine/prompts"),
        "--hidden-import",
        "gm_engine.interaction.control_processor",
        "--hidden-import",
        "gm_engine.interaction.pipecat_rlm_processor",
        "--hidden-import",
        "gm_engine.interaction.pipecat_ws_serializer",
        "--hidden-import",
        "gm_engine.interaction.bot_speaking_state",
        "--hidden-import",
        "gm_engine.interaction.deepgram_stt",
        "--hidden-import",
        "gm_engine.llm.codex_provider",
        "--hidden-import",
        "pipecat.services.openai.stt",
        "--hidden-import",
        "pipecat.services.openai.tts",
        "--hidden-import",
        "pipecat.services.deepgram.stt",
        "--hidden-import",
        "pipecat.services.elevenlabs.tts",
        "--hidden-import",
        "qdrant_client",
        str(ROOT / "scripts" / "proprietary_entry.py"),
    ]
    _run(cmd, cwd=ROOT)
    return dist_dir / "GMv3Server"


def _codex_npm_tag_and_vendor_triple() -> tuple[str, str]:
    sys_name = platform.system().lower()
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        arch = "x64"
    elif machine in {"aarch64", "arm64"}:
        arch = "arm64"
    else:
        raise RuntimeError(f"Unsupported CPU architecture for bundled Codex CLI: {machine}")

    if sys_name.startswith("win"):
        return (f"win32-{arch}", f"{'x86_64' if arch == 'x64' else 'aarch64'}-pc-windows-msvc")
    if sys_name == "darwin":
        return (f"darwin-{arch}", f"{'x86_64' if arch == 'x64' else 'aarch64'}-apple-darwin")
    if sys_name in {"linux"}:
        return (f"linux-{arch}", f"{'x86_64' if arch == 'x64' else 'aarch64'}-unknown-linux-musl")
    raise RuntimeError(f"Unsupported OS for bundled Codex CLI: {sys_name}")


def _bundle_codex_cli(*, app_dir: Path, stamp: str) -> None:
    platform_key, vendor_triple = _codex_npm_tag_and_vendor_triple()
    work_dir = ROOT / "build" / f"codex-bundle-{stamp}-{platform_key}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Pull target-specific native Codex CLI tarball from npm registry at build time.
    meta_req = Request("https://registry.npmjs.org/@openai/codex", headers={"Accept": "application/json"})
    with urlopen(meta_req, timeout=30) as resp:
        meta = json.load(resp)
    dist_tags = meta.get("dist-tags") or {}
    tagged_version = str(dist_tags.get(platform_key) or "").strip()
    if not tagged_version:
        raise RuntimeError(f"Missing @openai/codex dist-tag for platform: {platform_key}")
    versions = meta.get("versions") or {}
    version_meta = versions.get(tagged_version) or {}
    tarball_url = str(((version_meta.get("dist") or {}).get("tarball")) or "").strip()
    if not tarball_url:
        raise RuntimeError(f"Missing tarball URL for @openai/codex version: {tagged_version}")

    tarball_path = work_dir / "codex.tgz"
    with urlopen(tarball_url, timeout=60) as src, tarball_path.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    with tarfile.open(tarball_path, "r:gz") as tf:
        tf.extractall(work_dir)

    vendor_root = work_dir / "package" / "vendor" / vendor_triple
    codex_src = vendor_root / "codex"
    path_src = vendor_root / "path"
    if not codex_src.exists():
        raise RuntimeError(f"Bundled Codex CLI missing expected path: {codex_src}")

    _copytree(codex_src, app_dir / "codex")
    if path_src.exists():
        _copytree(path_src, app_dir / "path")

    codex_bin = app_dir / "codex" / ("codex.exe" if os.name == "nt" else "codex")
    if not codex_bin.exists():
        raise RuntimeError(f"Bundled Codex binary not found after copy: {codex_bin}")
    _chmod_exec([codex_bin])
    if (app_dir / "path" / "rg").exists():
        _chmod_exec([app_dir / "path" / "rg"])


def _chmod_exec(paths: Iterable[Path]) -> None:
    if os.name == "nt":
        return
    for p in paths:
        if not p.exists():
            continue
        cur = p.stat().st_mode
        p.chmod(cur | 0o111)


def _build_package(stamp: str, *, output_root: Path) -> tuple[Path, list[Path]]:
    tag = _platform_tag()
    name = f"GMv3Pro-{tag}-{stamp}"
    pkg_dir = output_root / name
    pkg_dir.mkdir(parents=True, exist_ok=True)

    built_app_dir = _build_pyinstaller(stamp)
    _copytree(built_app_dir, pkg_dir / "app")
    _bundle_codex_cli(app_dir=pkg_dir / "app", stamp=stamp)
    shutil.copy2(ROOT / ".env.example", pkg_dir / ".env.example")

    removed = _remove_source_maps(pkg_dir / "app")
    print(f"[package] removed source maps: {removed}")

    run_sh = textwrap.dedent(
        """\
        #!/usr/bin/env bash
        set -euo pipefail
        ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        cd "$ROOT_DIR"
        if [[ ! -f .env ]]; then
          cp .env.example .env
          echo "Created .env from template. Fill API keys and rerun."
        fi
        CODEX_BIN="$ROOT_DIR/app/codex/codex"
        CODEX_PATH_DIR="$ROOT_DIR/app/path"
        CODEX_HOME_DIR="$ROOT_DIR/.codex-home"
        mkdir -p "$CODEX_HOME_DIR"
        export CODEX_HOME="$CODEX_HOME_DIR"
        if [[ -x "$CODEX_BIN" ]]; then
          export GM_CODEX_BIN="$CODEX_BIN"
          if [[ -d "$CODEX_PATH_DIR" ]]; then
            export PATH="$CODEX_PATH_DIR:$PATH"
          fi
        fi
        exec "$ROOT_DIR/app/GMv3Server" --mode voice-ws
        """
    )
    _write_text(pkg_dir / "run.sh", run_sh)

    install_sh = textwrap.dedent(
        """\
        #!/usr/bin/env bash
        set -euo pipefail

        SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        DEFAULT_TARGET="${HOME}/.local/GMv3Pro"
        TARGET_DIR="${DEFAULT_TARGET}"

        if [[ -t 0 ]]; then
          echo "GMv3 Pro first-time installer (Linux/macOS)"
          echo "Default install path: ${DEFAULT_TARGET}"
          read -r -p "Install path (press Enter for default): " INPUT_TARGET
          if [[ -n "${INPUT_TARGET:-}" ]]; then
            TARGET_DIR="${INPUT_TARGET}"
          fi
        fi

        mkdir -p "$TARGET_DIR"
        cp -a "$SRC_DIR/app" "$TARGET_DIR/"
        cp -a "$SRC_DIR/.env.example" "$TARGET_DIR/"
        cp -a "$SRC_DIR/run.sh" "$TARGET_DIR/"
        chmod +x "$TARGET_DIR/run.sh"

        ENV_PATH="$TARGET_DIR/.env"
        if [[ ! -f "$ENV_PATH" ]]; then
          cp "$TARGET_DIR/.env.example" "$ENV_PATH"
        fi
        CODEX_BIN="$TARGET_DIR/app/codex/codex"
        CODEX_PATH_DIR="$TARGET_DIR/app/path"
        CODEX_HOME_DIR="$TARGET_DIR/.codex-home"
        mkdir -p "$CODEX_HOME_DIR"

        set_env_value() {
          local key="$1"
          local val="$2"
          local file="$3"
          local tmp
          tmp="$(mktemp)"
          awk -v k="$key" -v v="$val" '
            BEGIN { done=0 }
            $0 ~ ("^" k "=") { print k "=" v; done=1; next }
            { print }
            END { if (!done) print k "=" v }
          ' "$file" > "$tmp"
          mv "$tmp" "$file"
        }

        AUTH_MODE="openai"
        if [[ -t 0 ]]; then
          echo
          echo "Choose LLM auth mode:"
          echo "  1) OpenAI API key (GM_LLM_PROVIDER=openai)"
          echo "  2) ChatGPT Codex login (GM_LLM_PROVIDER=codex_chatgpt)"
          read -r -p "Select [1/2] (default 1): " AUTH_CHOICE
          if [[ "${AUTH_CHOICE:-1}" == "2" ]]; then
            AUTH_MODE="codex_chatgpt"
          fi
        fi
        set_env_value "GM_LLM_PROVIDER" "$AUTH_MODE" "$ENV_PATH"

        if [[ -t 0 ]]; then
          echo
          if [[ "$AUTH_MODE" == "openai" ]]; then
            echo "Set OPENAI_API_KEY in: $ENV_PATH"
          else
            echo "ChatGPT Codex auth mode selected."
            echo "Bundled Codex binary: $CODEX_BIN"
            echo "Auth storage (CODEX_HOME): $CODEX_HOME_DIR"
          fi
          read -r -p "Open .env now? [Y/n]: " OPEN_ENV
          if [[ -z "${OPEN_ENV:-}" || "${OPEN_ENV,,}" == "y" || "${OPEN_ENV,,}" == "yes" ]]; then
            if [[ -n "${EDITOR:-}" ]] && command -v "${EDITOR%% *}" >/dev/null 2>&1; then
              "${EDITOR%% *}" "$ENV_PATH" || true
            elif command -v nano >/dev/null 2>&1; then
              nano "$ENV_PATH"
            elif command -v vi >/dev/null 2>&1; then
              vi "$ENV_PATH"
            else
              echo "No terminal editor found. Edit manually: $ENV_PATH"
            fi
          fi

          if [[ "$AUTH_MODE" == "codex_chatgpt" ]]; then
            echo
            read -r -p "Run ChatGPT login now (opens browser)? [Y/n]: " RUN_CODEX_LOGIN
            if [[ -z "${RUN_CODEX_LOGIN:-}" || "${RUN_CODEX_LOGIN,,}" == "y" || "${RUN_CODEX_LOGIN,,}" == "yes" ]]; then
              if [[ -x "$CODEX_BIN" ]]; then
                export CODEX_HOME="$CODEX_HOME_DIR"
                export GM_CODEX_BIN="$CODEX_BIN"
                if [[ -d "$CODEX_PATH_DIR" ]]; then
                  export PATH="$CODEX_PATH_DIR:$PATH"
                fi
                "$CODEX_BIN" login || echo "Codex login failed; you can retry later with: $CODEX_BIN login"
              else
                echo "Bundled Codex binary not found: $CODEX_BIN"
              fi
            fi
          fi
        fi

        cat <<MSG
        Installed to: $TARGET_DIR

        Next steps:
          1) cd "$TARGET_DIR"
          2) Review .env and add provider keys
          3) Start server: ./run.sh
          4) Open: http://localhost:8000
        MSG

        if [[ -t 0 ]]; then
          echo
          read -r -p "Start now? [y/N]: " START_NOW
          if [[ "${START_NOW:-n}" == "y" || "${START_NOW:-n}" == "Y" ]]; then
            cd "$TARGET_DIR"
            exec ./run.sh
          fi
        fi
        """
    )
    _write_text(pkg_dir / "install.sh", install_sh)

    windows_launcher_bat = textwrap.dedent(
        r"""\
        @echo off
        setlocal EnableExtensions EnableDelayedExpansion
        cd /d "%~dp0"

        title GMv3 Pro Launcher
        for /f %%e in ('echo prompt $E ^| cmd') do set "ESC=%%e"
        set "C_OK=!ESC![92m"
        set "C_WARN=!ESC![93m"
        set "C_INFO=!ESC![96m"
        set "C_TITLE=!ESC![95m"
        set "C_RESET=!ESC![0m"

        set "APP_EXE=app\GMv3Server.exe"
        set "ENV_FILE=.env"
        set "ENV_TEMPLATE=.env.example"
        set "CODEX_HOME=%~dp0.codex-home"
        set "CODEX_BIN=%~dp0app\codex\codex.exe"
        set "CODEX_PATH=%~dp0app\path"

        if exist "%CODEX_PATH%" set "PATH=%CODEX_PATH%;%PATH%"
        if not exist "%CODEX_HOME%" mkdir "%CODEX_HOME%" >nul 2>&1
        set "GM_CODEX_BIN=%CODEX_BIN%"

        if not exist "%APP_EXE%" (
          call :print_error "Missing %APP_EXE%. Re-extract this package and try again."
          pause
          endlocal & exit /b 1
        )

        if /I "%~1"=="--run" goto run_server
        if /I "%~1"=="--setup" goto setup_config
        if /I "%~1"=="--login" goto codex_login
        if /I "%~1"=="--env" goto edit_env

        if not exist "%ENV_FILE%" (
          call :print_info "First-time setup detected. Let's configure your app."
          call :setup_config
          if errorlevel 1 (
            call :print_error "Setup did not complete."
            pause
            endlocal & exit /b 1
          )
        )

        :main_menu
        cls
        call :print_banner
        echo !C_INFO!1^) Start GMv3 Pro!C_RESET!
        echo !C_INFO!2^) Setup / Change configuration!C_RESET!
        echo !C_INFO!3^) ChatGPT Login ^(Codex^)!C_RESET!
        echo !C_INFO!4^) Open .env in Notepad!C_RESET!
        echo !C_INFO!5^) Exit!C_RESET!
        echo.
        set /p CHOICE=Choose [1-5]: 

        if "%CHOICE%"=="1" goto run_server
        if "%CHOICE%"=="2" goto setup_config
        if "%CHOICE%"=="3" goto codex_login
        if "%CHOICE%"=="4" goto edit_env
        if "%CHOICE%"=="5" goto done

        call :print_warn "Invalid option. Please choose 1-5."
        timeout /t 1 >nul
        goto main_menu

        :run_server
        if not exist "%ENV_FILE%" (
          call :setup_config
          if errorlevel 1 goto main_menu
        )
        call :print_info "Starting GMv3 server..."
        "%APP_EXE%" --mode voice-ws
        set EXIT_CODE=%ERRORLEVEL%
        if not "%EXIT_CODE%"=="0" (
          call :print_warn "Server exited with code %EXIT_CODE%."
          pause
        )
        if /I "%~1"=="--run" endlocal & exit /b %EXIT_CODE%
        goto main_menu

        :setup_config
        call :ensure_env
        if errorlevel 1 exit /b 1

        call :print_info "Choose LLM auth mode:"
        echo   1^) OpenAI API key ^(GM_LLM_PROVIDER=openai^)
        echo   2^) ChatGPT Codex login ^(GM_LLM_PROVIDER=codex_chatgpt^)
        set /p AUTH_CHOICE=Select [1/2] (default 1): 
        set "AUTH_MODE=openai"
        if "%AUTH_CHOICE%"=="2" set "AUTH_MODE=codex_chatgpt"

        powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "& { $path='%ENV_FILE%'; $key='GM_LLM_PROVIDER'; $value='%AUTH_MODE%'; $lines=if (Test-Path $path) { Get-Content -Path $path } else { @() }; $re='^'+[regex]::Escape($key)+'='; $done=$false; for($i=0;$i -lt $lines.Count;$i++){ if($lines[$i] -match $re){ $lines[$i] = "$key=$value"; $done=$true } }; if(-not $done){ $lines += "$key=$value" }; Set-Content -Path $path -Value $lines -Encoding UTF8 }"
        if errorlevel 1 (
          call :print_error "Failed to update %ENV_FILE%."
          exit /b 1
        )

        if /I "%AUTH_MODE%"=="openai" (
          call :print_ok "OpenAI mode selected. Set OPENAI_API_KEY in %ENV_FILE%."
        ) else (
          call :print_ok "ChatGPT Codex mode selected."
          echo Bundled Codex binary: %CODEX_BIN%
          echo Auth storage ^(CODEX_HOME^): %CODEX_HOME%
        )

        set /p OPEN_ENV=Open %ENV_FILE% in Notepad now? [Y/n]: 
        if "%OPEN_ENV%"=="" set "OPEN_ENV=Y"
        if /I "%OPEN_ENV%"=="Y" call :edit_env_inline
        if /I "%OPEN_ENV%"=="YES" call :edit_env_inline

        if /I "%AUTH_MODE%"=="codex_chatgpt" (
          set /p RUN_LOGIN=Run ChatGPT login now (opens browser)? [Y/n]: 
          if "%RUN_LOGIN%"=="" set "RUN_LOGIN=Y"
          if /I "%RUN_LOGIN%"=="Y" call :codex_login
          if /I "%RUN_LOGIN%"=="YES" call :codex_login
        )

        if /I "%~1"=="--setup" endlocal & exit /b 0
        goto main_menu

        :edit_env
        call :ensure_env
        if errorlevel 1 (
          if /I "%~1"=="--env" endlocal & exit /b 1
          goto main_menu
        )
        notepad "%ENV_FILE%"
        if /I "%~1"=="--env" endlocal & exit /b 0
        goto main_menu

        :edit_env_inline
        notepad "%ENV_FILE%"
        exit /b 0

        :codex_login
        if not exist "%CODEX_BIN%" (
          call :print_error "Bundled Codex binary not found: %CODEX_BIN%"
          if /I "%~1"=="--login" endlocal & exit /b 1
          pause
          goto main_menu
        )
        call :print_info "Opening ChatGPT login with Codex..."
        "%CODEX_BIN%" login
        set LOGIN_EXIT=%ERRORLEVEL%
        if not "%LOGIN_EXIT%"=="0" (
          call :print_warn "ChatGPT login failed with code %LOGIN_EXIT%."
          if /I "%~1"=="--login" endlocal & exit /b %LOGIN_EXIT%
          pause
          goto main_menu
        )
        call :print_ok "ChatGPT login completed."
        if /I "%~1"=="--login" endlocal & exit /b 0
        goto main_menu

        :ensure_env
        if not exist "%ENV_FILE%" (
          if not exist "%ENV_TEMPLATE%" (
            call :print_error "Missing %ENV_TEMPLATE% template."
            exit /b 1
          )
          copy /Y "%ENV_TEMPLATE%" "%ENV_FILE%" >nul
        )
        exit /b 0

        :print_banner
        echo !C_TITLE!=============================================================!C_RESET!
        echo !C_TITLE!   ____ __  ____   __    ___     ____            __          !C_RESET!
        echo !C_TITLE!  / ___/  |/  / | / /   /   |   / __ \_________  / /___  ____ !C_RESET!
        echo !C_TITLE! / /  / /|_/ /  |/ /   / /| |  / /_/ / ___/ __ \/ / __ \/ __ \!C_RESET!
        echo !C_TITLE!/ /__/ /  / / /|  /   / ___ | / ____/ /  / /_/ / / /_/ / / / /!C_RESET!
        echo !C_TITLE!\___/_/  /_/_/ |_/   /_/  |_|/_/   /_/   \____/_/\____/_/ /_/ !C_RESET!
        echo !C_TITLE!=============================================================!C_RESET!
        echo.
        exit /b 0

        :print_info
        echo !C_INFO![INFO]!C_RESET! %~1
        exit /b 0

        :print_ok
        echo !C_OK![OK]!C_RESET! %~1
        exit /b 0

        :print_warn
        echo !C_WARN![WARN]!C_RESET! %~1
        exit /b 0

        :print_error
        echo !C_WARN![ERROR]!C_RESET! %~1
        exit /b 0

        :done
        endlocal & exit /b 0
        """
    )
    _write_text(pkg_dir / "START_GMv3Pro.bat", windows_launcher_bat)

    windows_wrappers_dir = pkg_dir / "_advanced_windows"
    windows_wrappers_dir.mkdir(parents=True, exist_ok=True)

    run_bat = textwrap.dedent(
        r"""\
        @echo off
        cd /d "%~dp0\.."
        call "%~dp0\..\START_GMv3Pro.bat" --run
        """
    )
    _write_text(windows_wrappers_dir / "run.bat", run_bat)

    install_bat = textwrap.dedent(
        r"""\
        @echo off
        cd /d "%~dp0\.."
        call "%~dp0\..\START_GMv3Pro.bat" --setup
        """
    )
    _write_text(windows_wrappers_dir / "install.bat", install_bat)

    chatgpt_login_bat = textwrap.dedent(
        r"""\
        @echo off
        cd /d "%~dp0\.."
        call "%~dp0\..\START_GMv3Pro.bat" --login
        """
    )
    _write_text(windows_wrappers_dir / "chatgpt-login.bat", chatgpt_login_bat)

    readme = textwrap.dedent(
        f"""\
        # GMv3 Pro Binary ({tag})

        This package is a compiled distribution.

        ## LLM auth modes (alternative)
        - OpenAI API mode: `GM_LLM_PROVIDER=openai` + `OPENAI_API_KEY=...`
        - ChatGPT Codex mode: `GM_LLM_PROVIDER=codex_chatgpt` + bundled `codex login`

        ## First install
        Linux/macOS (guided):
        ```bash
        ./install.sh
        ```

        Windows (single-file launcher):
        - Double-click `START_GMv3Pro.bat`.
        - First launch guides auth mode and `.env` setup with a colorful ASCII menu.

        The Windows launcher includes:
        - Start server
        - Setup/change auth mode
        - Open `.env`
        - ChatGPT login (bundled Codex CLI)
        - Colorized output + ASCII launcher banner

        Next launches:
        - Linux/macOS: `./run.sh`
        - Windows: double-click `START_GMv3Pro.bat`

        Advanced wrappers (for power users) are available under `_advanced_windows/`.

        ## Required
        - Choose one LLM auth mode:
          - OpenAI API (`OPENAI_API_KEY`)
          - ChatGPT Codex (bundled `codex login` + `GM_LLM_PROVIDER=codex_chatgpt`)

        Optional:
        - `DEEPGRAM_API_KEY`
        - `ELEVENLABS_API_KEY`

        ## Highlights
        - Self-contained package: app runtime + dependencies + bundled Codex CLI.
        - VAD-based live turn detection and interruption.
        - Multi-player voice detection flow (up to 8 players).
        - RLM + LLM + Qdrant rulebook retrieval.
        - Real-time voice options with OpenAI, Deepgram, and ElevenLabs.

        ## Note
        This package is hardened and ships no plain project source.
        Absolute reverse-engineering prevention is not technically guaranteed.
        """
    )
    _write_text(pkg_dir / "README.md", readme)

    license_text = textwrap.dedent(
        """\
        Copyright (c) 2026.
        All rights reserved.

        This software is licensed, not sold.
        You may install and use one copy for internal use.
        You may not modify, redistribute, sublicense, reverse engineer,
        decompile, disassemble, or create derivative works except where
        applicable law explicitly permits despite this restriction.
        """
    )
    _write_text(pkg_dir / "LICENSE.txt", "Proprietary Software License\n\n" + license_text)
    _chmod_exec([pkg_dir / "run.sh", pkg_dir / "install.sh"])

    artifacts: list[Path] = []
    zip_path = output_root / f"{name}.zip"
    _zip_dir(pkg_dir, zip_path)
    artifacts.append(zip_path)
    if os.name != "nt":
        tgz_path = output_root / f"{name}.tar.gz"
        _tar_dir(pkg_dir, tgz_path)
        artifacts.append(tgz_path)
    return pkg_dir, artifacts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=str(ROOT / ".." / "GMv3-proprietary-universal-dist"))
    ap.add_argument("--stamp", default="")
    args = ap.parse_args()

    stamp = args.stamp or datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)

    pkg_dir, artifacts = _build_package(stamp, output_root=out)
    print(f"[done] package dir: {pkg_dir}")
    for a in artifacts:
        print(f"[done] artifact: {a}")


if __name__ == "__main__":
    main()
