from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


def _resolved_codex_bin(preferred: str | None = None) -> str:
    p = str(preferred or "").strip()
    if p and p != "codex":
        return p

    env_bin = str(os.environ.get("GM_CODEX_BIN") or "").strip()
    if env_bin:
        return env_bin

    exe_dir = Path(sys.executable).resolve().parent
    ext = ".exe" if os.name == "nt" else ""
    candidates = [
        exe_dir / "codex" / f"codex{ext}",
        exe_dir.parent / "app" / "codex" / f"codex{ext}",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "codex"


def _codex_env(codex_bin: str) -> dict[str, str]:
    env = dict(os.environ)
    bin_path = Path(codex_bin)
    path_dir = bin_path.parent.parent / "path"
    if path_dir.exists():
        sep = ";" if os.name == "nt" else ":"
        cur = env.get("PATH", "")
        env["PATH"] = f"{path_dir}{sep}{cur}" if cur else str(path_dir)
    return env


@dataclass
class CodexChatGPTLLM:
    """LLM provider that uses `codex exec` with ChatGPT login/session auth.

    This allows running the app without an OpenAI API key when the user is already
    authenticated via `codex login` (device auth / ChatGPT account).
    """

    model: str = "gpt-5"
    codex_bin: str = ""
    timeout_secs: float = 120.0

    @classmethod
    def login_status(cls, *, codex_bin: str = "") -> tuple[bool, str]:
        chosen_bin = _resolved_codex_bin(codex_bin)
        try:
            out = subprocess.run(
                [chosen_bin, "login", "status"],
                check=False,
                capture_output=True,
                text=True,
                timeout=8.0,
                env=_codex_env(chosen_bin),
            )
        except Exception as e:
            return (False, f"codex login status failed: {e}")
        msg = (out.stdout or out.stderr or "").strip()
        if out.returncode != 0:
            return (False, msg or f"codex login status exit={out.returncode}")
        if "Logged in" in msg:
            return (True, msg)
        return (False, msg or "codex is not logged in")

    async def complete(self, *, system: str, user: str, temperature: float = 0.2) -> str:
        # codex CLI does not currently expose temperature as a stable non-interactive flag.
        _ = temperature
        return await asyncio.to_thread(self._complete_blocking, system=system, user=user)

    def _complete_blocking(self, *, system: str, user: str) -> str:
        chosen_bin = _resolved_codex_bin(self.codex_bin)
        prompt = (
            "You are the LLM backend for a tabletop game master.\n"
            "Return only the answer text.\n\n"
            f"SYSTEM:\n{system}\n\n"
            f"USER:\n{user}\n"
        )
        with tempfile.NamedTemporaryFile(prefix="gm_codex_", suffix=".txt", delete=False) as tf:
            out_path = Path(tf.name)
        try:
            cmd = [
                chosen_bin,
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-c",
                'model_reasoning_effort="high"',
                "--output-last-message",
                str(out_path),
                "--ephemeral",
            ]
            if str(self.model or "").strip():
                cmd.extend(["-m", str(self.model).strip()])
            cmd.append(prompt)

            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=float(self.timeout_secs),
                env=_codex_env(chosen_bin),
            )
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "").strip()
                raise RuntimeError(f"codex exec failed (exit={proc.returncode}): {err[:600]}")
            text = out_path.read_text(encoding="utf-8", errors="ignore").strip()
            if text:
                return text
            # Fallback if output file wasn't populated for any reason.
            alt = (proc.stdout or "").strip()
            if alt:
                return alt
            raise RuntimeError("codex exec returned empty output.")
        finally:
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass
