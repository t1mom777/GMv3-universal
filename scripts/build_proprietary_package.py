from __future__ import annotations

import argparse
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
    if shutil.which("npm") is None:
        raise RuntimeError("npm is required at build time to bundle Codex CLI.")
    npm_tag, vendor_triple = _codex_npm_tag_and_vendor_triple()
    work_dir = ROOT / "build" / f"codex-bundle-{stamp}-{npm_tag}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    _write_text(
        work_dir / "package.json",
        '{\n  "name": "gmv3-codex-bundle",\n  "private": true,\n  "version": "1.0.0"\n}\n',
    )

    # Pull target-specific native Codex CLI bundle at build time so installer stays one-click/offline.
    _run(
        [
            "npm",
            "install",
            "--no-fund",
            "--no-audit",
            "--omit=dev",
            f"@openai/codex@{npm_tag}",
        ],
        cwd=work_dir,
    )

    vendor_root = work_dir / "node_modules" / "@openai" / "codex" / "vendor" / vendor_triple
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

    run_bat = textwrap.dedent(
        """\
        @echo off
        setlocal
        cd /d "%~dp0"
        if not exist ".env" (
          copy /Y ".env.example" ".env" >nul
          echo Created .env from template. Fill API keys and rerun.
        )
        set "CODEX_HOME=%~dp0.codex-home"
        if not exist "%CODEX_HOME%" mkdir "%CODEX_HOME%" >nul 2>&1
        set "GM_CODEX_BIN=%~dp0app\\codex\\codex.exe"
        if exist "%~dp0app\\path" set "PATH=%~dp0app\\path;%PATH%"
        if not exist "app\\GMv3Server.exe" (
          echo ERROR: app\\GMv3Server.exe was not found.
          pause
          endlocal & exit /b 1
        )
        "app\\GMv3Server.exe" --mode voice-ws
        set EXIT_CODE=%ERRORLEVEL%
        if not "%EXIT_CODE%"=="0" (
          echo.
          echo Server exited with code %EXIT_CODE%.
          pause
        )
        endlocal & exit /b %EXIT_CODE%
        """
    )
    _write_text(pkg_dir / "run.bat", run_bat)

    install_bat = textwrap.dedent(
        """\
        @echo off
        setlocal
        cd /d "%~dp0"
        echo Starting GMv3 Pro Windows installer...
        powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
        set EXIT_CODE=%ERRORLEVEL%
        if not "%EXIT_CODE%"=="0" (
          echo.
          echo Installer failed with code %EXIT_CODE%.
          echo If needed, right-click install.ps1 and run it with PowerShell.
          pause
          endlocal & exit /b %EXIT_CODE%
        )
        echo.
        echo Installer finished.
        pause
        endlocal & exit /b 0
        """
    )
    _write_text(pkg_dir / "install.bat", install_bat)

    install_ps1 = textwrap.dedent(
        """\
        $ErrorActionPreference = "Stop"
        $src = Split-Path -Parent $MyInvocation.MyCommand.Path
        $defaultTarget = Join-Path $env:USERPROFILE "GMv3Pro"
        $target = $defaultTarget

        Write-Host "GMv3 Pro first-time installer (Windows)"
        Write-Host "Default install path: $defaultTarget"
        $inputTarget = Read-Host "Install path (press Enter for default)"
        if (-not [string]::IsNullOrWhiteSpace($inputTarget)) {
          $target = $inputTarget.Trim()
        }

        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -Recurse -Force (Join-Path $src "app") $target
        Copy-Item -Force (Join-Path $src ".env.example") $target
        Copy-Item -Force (Join-Path $src "run.bat") $target
        Copy-Item -Force (Join-Path $src "install.ps1") $target
        if (Test-Path (Join-Path $src "install.bat")) {
          Copy-Item -Force (Join-Path $src "install.bat") $target
        }

        $envPath = Join-Path $target ".env"
        if (-not (Test-Path $envPath)) {
          Copy-Item -Force (Join-Path $target ".env.example") $envPath
        }

        function Set-EnvValue {
          param(
            [string]$Path,
            [string]$Key,
            [string]$Value
          )
          $lines = @()
          if (Test-Path $Path) {
            $lines = Get-Content -Path $Path
          }
          $prefix = [regex]::Escape($Key) + "="
          $found = $false
          for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match "^$prefix") {
              $lines[$i] = "$Key=$Value"
              $found = $true
            }
          }
          if (-not $found) {
            $lines += "$Key=$Value"
          }
          Set-Content -Path $Path -Value $lines -Encoding UTF8
        }

        Write-Host ""
        Write-Host "Choose LLM auth mode:"
        Write-Host "  1) OpenAI API key (GM_LLM_PROVIDER=openai)"
        Write-Host "  2) ChatGPT Codex login (GM_LLM_PROVIDER=codex_chatgpt)"
        $authChoice = Read-Host "Select [1/2] (default 1)"
        $authMode = "openai"
        if ($authChoice -eq "2") {
          $authMode = "codex_chatgpt"
        }
        Set-EnvValue -Path $envPath -Key "GM_LLM_PROVIDER" -Value $authMode

        Write-Host ""
        $codexBin = Join-Path $target "app\\codex\\codex.exe"
        $codexPathDir = Join-Path $target "app\\path"
        $codexHome = Join-Path $target ".codex-home"
        New-Item -ItemType Directory -Force -Path $codexHome | Out-Null
        $env:CODEX_HOME = $codexHome
        if (Test-Path $codexPathDir) {
          $env:PATH = "$codexPathDir;$env:PATH"
        }
        if (Test-Path $codexBin) {
          $env:GM_CODEX_BIN = $codexBin
        }

        Write-Host ""
        if ($authMode -eq "openai") {
          Write-Host "Set OPENAI_API_KEY in: $envPath"
        } else {
          Write-Host "ChatGPT Codex auth mode selected."
          Write-Host "Bundled Codex binary: $codexBin"
          Write-Host "Auth storage (CODEX_HOME): $codexHome"
        }
        $openEnv = Read-Host "Open .env in Notepad now? [Y/n]"
        if (
          [string]::IsNullOrWhiteSpace($openEnv) -or
          $openEnv.Trim().ToLower() -eq "y" -or
          $openEnv.Trim().ToLower() -eq "yes"
        ) {
          notepad $envPath
        }

        if ($authMode -eq "codex_chatgpt") {
          $runLogin = Read-Host "Run ChatGPT login now (opens browser)? [Y/n]"
          $runLoginNorm = if ([string]::IsNullOrWhiteSpace($runLogin)) { "y" } else { $runLogin.Trim().ToLower() }
          if ($runLoginNorm -eq "y" -or $runLoginNorm -eq "yes") {
            if (Test-Path $codexBin) {
              & $codexBin login
              if ($LASTEXITCODE -ne 0) {
                Write-Warning "Codex login failed. Retry later with: $codexBin login"
              }
            } else {
              Write-Warning "Bundled Codex binary not found: $codexBin"
            }
          }
        }

        Write-Host ""
        Write-Host "Installed to: $target"
        Write-Host "Next steps:"
        Write-Host "  1) Review .env and add provider keys"
        Write-Host "  2) Start server: .\\run.bat"
        Write-Host "  3) Open: http://localhost:8000"
        Write-Host "  4) Re-run setup later: .\\install.bat"

        $startNow = Read-Host "Start now? [y/N]"
        if (-not [string]::IsNullOrWhiteSpace($startNow) -and $startNow.Trim().ToLower() -eq "y") {
          Set-Location $target
          & .\run.bat
        }
        """
    )
    _write_text(pkg_dir / "install.ps1", install_ps1)

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

        Windows (guided):
        - Double-click `install.bat`.
        - If needed, run `install.ps1`.

        The installer guides you through:
        - install location
        - `.env` creation
        - auth mode selection
        - optional browser-based ChatGPT login (bundled Codex CLI)
        - optional immediate launch
        - final localhost URL (`http://localhost:8000`)

        Next launches:
        - Linux/macOS: `./run.sh`
        - Windows: double-click `run.bat`

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
