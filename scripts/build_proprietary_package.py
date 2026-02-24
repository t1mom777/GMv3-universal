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
        exec "$ROOT_DIR/app/GMv3Server" --mode voice-ws
        """
    )
    _write_text(pkg_dir / "run.sh", run_sh)

    install_sh = textwrap.dedent(
        """\
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
        """
    )
    _write_text(pkg_dir / "install.sh", install_sh)

    run_bat = textwrap.dedent(
        """\
        @echo off
        setlocal
        cd /d %~dp0
        if not exist .env (
          copy /Y .env.example .env >nul
          echo Created .env from template. Fill API keys and rerun.
        )
        app\\GMv3Server.exe --mode voice-ws
        endlocal
        """
    )
    _write_text(pkg_dir / "run.bat", run_bat)

    install_ps1 = textwrap.dedent(
        """\
        $ErrorActionPreference = "Stop"
        $src = Split-Path -Parent $MyInvocation.MyCommand.Path
        $target = Join-Path $env:USERPROFILE "GMv3Pro"
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -Recurse -Force (Join-Path $src "app") $target
        Copy-Item -Force (Join-Path $src ".env.example") $target
        Copy-Item -Force (Join-Path $src "run.bat") $target
        Write-Host "Installed to: $target"
        Write-Host "Next: copy .env.example to .env, set keys, then run run.bat"
        """
    )
    _write_text(pkg_dir / "install.ps1", install_ps1)

    readme = textwrap.dedent(
        f"""\
        # GMv3 Pro Binary ({tag})

        This package is a compiled distribution.

        ## Quick start
        Linux/macOS:
        ```bash
        ./install.sh
        # or portable:
        ./run.sh
        ```

        Windows:
        - Run `install.ps1` in PowerShell
        - Or run portable: `run.bat`

        ## Required
        - `OPENAI_API_KEY` OR ChatGPT login fallback (`codex login`, set `GM_LLM_PROVIDER=codex_chatgpt`)

        Optional:
        - `DEEPGRAM_API_KEY`
        - `ELEVENLABS_API_KEY`

        ## Highlights
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
