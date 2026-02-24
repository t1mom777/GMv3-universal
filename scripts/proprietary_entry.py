from __future__ import annotations

import os
import sys
from pathlib import Path


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def main() -> None:
    root = _runtime_root()
    os.environ.setdefault("GM_ROOT", str(root))
    os.chdir(root)
    if len(sys.argv) == 1:
        sys.argv.extend(["--mode", "voice-ws"])
    from scripts.run_voice_gm import main as run_main

    run_main()


if __name__ == "__main__":
    main()
