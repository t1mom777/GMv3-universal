from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path


def _read_checksums(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        rows.append((parts[0], parts[1]))
    return rows


def _platform_label(filename: str) -> str:
    lower = filename.lower()
    if "linux-" in lower:
        return "Linux"
    if "windows-" in lower:
        return "Windows"
    if "macos-" in lower:
        return "macOS"
    return "Other"


def _render(version: str, checksums: list[tuple[str, str]]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    archives = [(h, f) for (h, f) in checksums if f.endswith(".zip") or f.endswith(".tar.gz")]

    by_platform: dict[str, list[str]] = {"Linux": [], "Windows": [], "macOS": [], "Other": []}
    for _, name in archives:
        by_platform[_platform_label(name)].append(name)
    for key in by_platform:
        by_platform[key].sort()

    lines: list[str] = []
    lines.append(f"# GMv3 {version}")
    lines.append("")
    lines.append(f"Release date (UTC): {now}")
    lines.append("")
    lines.append("## One-Click Downloads")
    if archives:
        for label in ("Linux", "Windows", "macOS", "Other"):
            files = by_platform[label]
            if not files:
                continue
            lines.append(f"- **{label}**")
            for name in files:
                lines.append(f"  - `{name}`")
    else:
        lines.append("- Release assets are attached below.")
    lines.append("")
    lines.append("## Included")
    lines.append("- Compiled app package (no plain project source files shipped).")
    lines.append("- Installer/run scripts for the target OS.")
    lines.append("- `.env.example` for configuration.")
    lines.append("- VAD turn detection + multilingual real-time voice pipeline.")
    lines.append("- Multi-player voice/speaker detection flow with persistent player profiles.")
    lines.append("- RLM + LLM + Qdrant retrieval stack for rulebook-grounded gameplay.")
    lines.append("- ChatGPT account fallback for LLM turns (`GM_LLM_PROVIDER=codex_chatgpt` + `codex login`).")
    lines.append("")
    lines.append("## Notes")
    lines.append("- STT/TTS providers still require their own credentials.")
    lines.append("- Reverse engineering cannot be made impossible on client software; this release is hardened best-effort.")
    lines.append("")
    if checksums:
        lines.append("## SHA256")
        lines.append("```text")
        for digest, name in checksums:
            lines.append(f"{digest}  {name}")
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--checksums", required=False, default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    checksum_rows = _read_checksums(Path(args.checksums)) if args.checksums else []
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_render(args.version, checksum_rows), encoding="utf-8")
    print(f"[done] release notes: {out_path}")


if __name__ == "__main__":
    main()
