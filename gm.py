from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def _voice_venv_python(root: Path) -> Path:
    if sys.platform == "win32":  # pragma: no cover
        return root / ".venv-voice" / "Scripts" / "python.exe"
    return root / ".venv-voice" / "bin" / "python"


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _wants_voice_ws(argv: list[str]) -> bool:
    """Return True if this invocation should run voice-ws (default)."""
    if "--mode" not in argv:
        return True
    try:
        i = argv.index("--mode")
        return (i + 1) < len(argv) and argv[i + 1].strip() == "voice-ws"
    except Exception:
        return True


def _should_reexec_into_voice_env() -> bool:
    wants_voice = _wants_voice_ws(sys.argv)

    # Voice mode requires Python <3.14 (pipecat-ai / numba constraint).
    if wants_voice and sys.version_info >= (3, 14):
        return True

    # If core deps are missing, we are likely running outside the project venv.
    if not _has_module("sqlalchemy"):
        return True

    # If voice deps are missing but voice is requested, jump into the voice venv.
    if wants_voice and not _has_module("pipecat"):
        return True

    return False


def _maybe_reexec() -> None:
    root = Path(__file__).resolve().parent
    voice_py = _voice_venv_python(root)

    need_reexec = _should_reexec_into_voice_env()
    if not voice_py.exists():
        if need_reexec:
            if _wants_voice_ws(sys.argv):
                msg = f"""Voice mode requires Python 3.13 (pipecat-ai does not support CPython 3.14).
Missing voice venv python: {voice_py}

Create it (recommended):
  pip install uv
  uv venv -p 3.13 .venv-voice
  . .venv-voice/bin/activate
  python -m pip install -e '.[voice,knowledge]'

Then run:
  python gm.py

(If you only want text mode, run: python gm.py --mode text)
"""
            else:
                msg = """Missing dependencies (sqlalchemy, etc.).

Install core deps:
  python -m venv .venv
  . .venv/bin/activate
  python -m pip install -U pip
  python -m pip install -e .
"""
            sys.stderr.write(msg)
            raise SystemExit(1)
        return

    try:
        if Path(sys.executable).resolve() == voice_py.resolve():
            return
    except Exception:
        pass

    if not need_reexec:
        return

    argv_tail = sys.argv[1:] if len(sys.argv) > 1 else ["--mode", "voice-ws"]
    os.execv(str(voice_py), [str(voice_py), str(Path(__file__).resolve()), *argv_tail])


if __name__ == "__main__":
    # Make `python gm.py` work even if the user didn't activate the voice venv.
    _maybe_reexec()

    # Default: run the browser + websocket voice GM with no extra arguments.
    if len(sys.argv) == 1:
        sys.argv.extend(["--mode", "voice-ws"])

    from scripts.run_voice_gm import main

    main()
