from __future__ import annotations

import array
import asyncio
import html
import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import parse as urlparse
from urllib import request as urlrequest

from gm_engine.app.settings_store import SettingsStore
from gm_engine.interaction.control_frames import GMClientMessageFrame
from gm_engine.knowledge.manager import KnowledgeManager
from gm_engine.llm.codex_provider import CodexChatGPTLLM
from gm_engine.rlm.controller import RLMController
from gm_engine.rlm.types import RetrievalSpec, StateReadSpec, TurnContext
from gm_engine.state.store import WorldStateStore

try:
    from pipecat.frames.frames import (
        InputAudioRawFrame,
        OutputTransportMessageUrgentFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
except ModuleNotFoundError:  # pragma: no cover
    FrameProcessor = object  # type: ignore[assignment,misc]
    FrameDirection = None  # type: ignore[assignment]
    OutputTransportMessageUrgentFrame = object  # type: ignore[assignment]
    InputAudioRawFrame = object  # type: ignore[assignment]
    VADUserStartedSpeakingFrame = object  # type: ignore[assignment]
    VADUserStoppedSpeakingFrame = object  # type: ignore[assignment]


def _ctx_from_settings(settings: Any, *, session_id: str) -> TurnContext:
    return TurnContext(
        campaign_id=str(settings.voice.campaign_id),
        session_id=session_id,
        turn_id="0",
        player_id=str(settings.voice.player_id),
        transcript_text="",
        locale=str(settings.voice.locale),
    )


def _extract_json_obj(s: str) -> dict[str, Any]:
    s = (s or "").strip()
    if not s:
        raise RuntimeError("Empty response from LLM.")
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # Attempt to decode the first JSON object from mixed output without regex.
    # Regex on very long model outputs can become expensive.
    dec = json.JSONDecoder()
    start = s.find("{")
    while start != -1 and start < len(s):
        try:
            obj, _end = dec.raw_decode(s[start:])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        start = s.find("{", start + 1)

    raise RuntimeError("Could not parse JSON from LLM output. Try again or adjust the style prompt.")


def _optional_bool(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Invalid boolean value: {v!r}")


def _rulebook_sync_signature(settings: Any) -> tuple[Any, ...]:
    k = settings.knowledge
    return (
        str(k.primary_rulebook_source or "").strip(),
        str(k.primary_rulebook_doc_choice or "").strip(),
        str(k.primary_rulebook_path or "").strip(),
        str(k.primary_rulebook_doc_id or "").strip(),
        str(k.primary_rulebook_ruleset or "").strip(),
        str(k.primary_rulebook_doc_kind or "").strip(),
        str(k.primary_rulebook_collection_target or "").strip(),
        bool(k.primary_rulebook_auto_ingest),
        bool(k.primary_rulebook_auto_activate),
    )


def _voice_profiles_from_settings(settings: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for p in list(getattr(settings.voice, "player_profiles", []) or [])[:8]:
        if isinstance(p, dict):
            pid = str(p.get("player_id") or "").strip()
            dn = str(p.get("display_name") or "").strip()
            vp = str(p.get("voice_profile") or "").strip()
        else:
            pid = str(getattr(p, "player_id", "") or "").strip()
            dn = str(getattr(p, "display_name", "") or "").strip()
            vp = str(getattr(p, "voice_profile", "") or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append({"player_id": pid, "display_name": dn or pid, "voice_profile": vp})
    return out


_ENV_SECRET_FIELDS: dict[str, bool] = {
    "OPENAI_API_KEY": True,
    "DEEPGRAM_API_KEY": True,
    "ELEVENLABS_API_KEY": True,
    "OPENAI_BASE_URL": False,
    "QDRANT_API_KEY": True,
}
_ENV_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_ENV_SIMPLE_VALUE_RE = re.compile(r"^[A-Za-z0-9_./:@+,\-=]*$")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _mask_secret(value: str, *, secret: bool) -> str:
    s = str(value or "")
    if not s:
        return ""
    if not secret:
        return s[:180]
    if len(s) <= 8:
        return "*" * len(s)
    return s[:4] + "..." + s[-3:]


def _dotenv_quote(value: str) -> str:
    s = str(value)
    if _ENV_SIMPLE_VALUE_RE.fullmatch(s):
        return s
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _apply_env_updates(updates: dict[str, str | None]) -> None:
    env_path = _repo_root() / ".env"
    lines: list[str | None]
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    index_by_key: dict[str, int] = {}
    for idx, line in enumerate(lines):
        m = _ENV_ASSIGN_RE.match(line)
        if not m:
            continue
        index_by_key[m.group(1)] = idx

    for key, value in updates.items():
        if key in index_by_key:
            idx = index_by_key[key]
            lines[idx] = None if value is None else f"{key}={_dotenv_quote(value)}"
        elif value is not None:
            lines.append(f"{key}={_dotenv_quote(value)}")

    compact = [ln for ln in lines if ln is not None]
    text = ("\n".join(compact).rstrip() + "\n") if compact else ""
    env_path.write_text(text, encoding="utf-8")

    for key, value in updates.items():
        if value is None:
            os.environ.pop(key, None)
            continue
        os.environ[key] = value
    if not str(os.environ.get("OPENAI_BASE_URL") or "").strip():
        os.environ.pop("OPENAI_BASE_URL", None)


def _secrets_payload() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, is_secret in _ENV_SECRET_FIELDS.items():
        raw = str(os.environ.get(key) or "")
        out[key] = {
            "present": bool(raw.strip()),
            "masked": _mask_secret(raw, secret=is_secret),
            "secret": is_secret,
        }
    return out


_GAME_SYSTEM_HINTS: list[dict[str, str]] = [
    {
        "name": "Numenera",
        "aliases": "cypher system monte cook",
        "setup_system": "numenera",
        "ruleset": "numenera",
        "beginner_hint": "Roll d20 against a target number, spend Effort to reduce difficulty, and keep turns short.",
        "url": "https://en.wikipedia.org/wiki/Numenera",
    },
    {
        "name": "Dungeons & Dragons 5th edition",
        "aliases": "dnd d&d 5e fifth edition",
        "setup_system": "dnd5e",
        "ruleset": "dnd5e",
        "beginner_hint": "Use d20 + modifier vs DC/AC, advantage-disadvantage for situational swings, keep actions explicit.",
        "url": "https://en.wikipedia.org/wiki/Editions_of_Dungeons_%26_Dragons#Dungeons_%26_Dragons_5th_edition",
    },
    {
        "name": "Pathfinder 2nd edition",
        "aliases": "pf2e pathfinder 2e",
        "setup_system": "pf2e",
        "ruleset": "pf2e",
        "beginner_hint": "Use the 3-action economy each turn and lean on condition tags when resolving effects.",
        "url": "https://en.wikipedia.org/wiki/Pathfinder_Roleplaying_Game#Second_edition",
    },
    {
        "name": "Call of Cthulhu",
        "aliases": "chaosium lovecraft percentile",
        "setup_system": "generic",
        "ruleset": "call_of_cthulhu",
        "beginner_hint": "Percentile skill checks are core; prioritize investigation and consequences over combat.",
        "url": "https://en.wikipedia.org/wiki/Call_of_Cthulhu_(role-playing_game)",
    },
    {
        "name": "Blades in the Dark",
        "aliases": "forged in the dark fitd",
        "setup_system": "generic",
        "ruleset": "blades_in_the_dark",
        "beginner_hint": "Action rolls use d6 pools; set position/effect first and use clocks to track pressure.",
        "url": "https://en.wikipedia.org/wiki/Blades_in_the_Dark",
    },
    {
        "name": "Savage Worlds",
        "aliases": "deadlands bennies wild die",
        "setup_system": "generic",
        "ruleset": "savage_worlds",
        "beginner_hint": "Trait rolls use die type + Wild Die; support fast combat and cinematic rulings.",
        "url": "https://en.wikipedia.org/wiki/Savage_Worlds",
    },
]


def _slug(s: str, *, fallback: str = "system") -> str:
    out = re.sub(r"[^a-z0-9]+", "_", str(s or "").strip().lower()).strip("_")
    return out or fallback


def _preset_match(name: str) -> dict[str, str] | None:
    n = str(name or "").strip().lower()
    if not n:
        return None
    for p in _GAME_SYSTEM_HINTS:
        hay = f"{p.get('name', '')} {p.get('aliases', '')}".lower()
        if n in hay or any(tok and tok in hay for tok in n.split()):
            return p
    return None


def _local_game_system_search(query: str, *, limit: int) -> list[dict[str, Any]]:
    q = str(query or "").strip().lower()
    out: list[dict[str, Any]] = []
    for p in _GAME_SYSTEM_HINTS:
        hay = f"{p.get('name', '')} {p.get('aliases', '')}".lower()
        if q and q not in hay and not any(tok and tok in hay for tok in q.split()):
            continue
        out.append(
            {
                "id": _slug(str(p.get("ruleset") or p.get("name") or "")),
                "name": str(p.get("name") or "Unknown"),
                "source": "preset",
                "summary": str(p.get("beginner_hint") or ""),
                "beginner_hint": str(p.get("beginner_hint") or ""),
                "ruleset": str(p.get("ruleset") or ""),
                "setup_system": str(p.get("setup_system") or "generic"),
                "url": str(p.get("url") or ""),
            }
        )
        if len(out) >= limit:
            break
    if out:
        return out
    # Helpful default list for empty/unknown search terms.
    for p in _GAME_SYSTEM_HINTS[:limit]:
        out.append(
            {
                "id": _slug(str(p.get("ruleset") or p.get("name") or "")),
                "name": str(p.get("name") or "Unknown"),
                "source": "preset",
                "summary": str(p.get("beginner_hint") or ""),
                "beginner_hint": str(p.get("beginner_hint") or ""),
                "ruleset": str(p.get("ruleset") or ""),
                "setup_system": str(p.get("setup_system") or "generic"),
                "url": str(p.get("url") or ""),
            }
        )
    return out


def _wiki_game_system_search(query: str, *, limit: int) -> list[dict[str, Any]]:
    q = str(query or "").strip()
    if not q:
        return []
    params = urlparse.urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": f"{q} tabletop role-playing game system",
            "srlimit": max(1, min(15, int(limit))),
            "utf8": "1",
            "format": "json",
        }
    )
    url = f"https://en.wikipedia.org/w/api.php?{params}"
    req = urlrequest.Request(url, headers={"User-Agent": "VoiceGameMaster/2.0"})
    with urlrequest.urlopen(req, timeout=8.0) as resp:  # noqa: S310 - controlled URL
        payload = json.loads(resp.read().decode("utf-8", errors="ignore"))

    out: list[dict[str, Any]] = []
    for row in (payload.get("query", {}) or {}).get("search", [])[:limit]:
        title = str((row or {}).get("title") or "").strip()
        if not title:
            continue
        snippet = str((row or {}).get("snippet") or "")
        summary = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(snippet))).strip()
        preset = _preset_match(title)
        ruleset = _slug(preset.get("ruleset") if preset else title)
        out.append(
            {
                "id": _slug(title),
                "name": title,
                "source": "wikipedia",
                "summary": summary,
                "beginner_hint": (
                    str(preset.get("beginner_hint"))
                    if preset and preset.get("beginner_hint")
                    else "Open the linked page for quick overview, then set a short campaign tone and rules strictness."
                ),
                "ruleset": ruleset,
                "setup_system": str(preset.get("setup_system") if preset else "generic"),
                "url": f"https://en.wikipedia.org/wiki/{urlparse.quote(title.replace(' ', '_'))}",
            }
        )
    return out


def _duckduckgo_flatten_topics(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("Topics"), list):
            out.extend(_duckduckgo_flatten_topics(item.get("Topics") or []))
            continue
        txt = str(item.get("Text") or "").strip()
        url = str(item.get("FirstURL") or "").strip()
        if txt or url:
            out.append({"text": txt, "url": url})
    return out


def _duckduckgo_game_system_search(query: str, *, limit: int) -> list[dict[str, Any]]:
    q = str(query or "").strip()
    if not q:
        return []
    params = urlparse.urlencode(
        {
            "q": f"{q} tabletop rpg system",
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        }
    )
    url = f"https://api.duckduckgo.com/?{params}"
    req = urlrequest.Request(url, headers={"User-Agent": "VoiceGameMaster/2.0"})
    with urlrequest.urlopen(req, timeout=8.0) as resp:  # noqa: S310 - controlled URL
        payload = json.loads(resp.read().decode("utf-8", errors="ignore"))

    topics = _duckduckgo_flatten_topics(list(payload.get("RelatedTopics") or []))
    out: list[dict[str, Any]] = []
    for item in topics[: max(2 * limit, 8)]:
        txt = str(item.get("text") or "").strip()
        if not txt:
            continue
        # Typical format: "Title - summary text"
        if " - " in txt:
            title, summary = txt.split(" - ", 1)
        else:
            title, summary = txt, txt
        title = str(title or "").strip()
        summary = str(summary or "").strip()
        if not title:
            continue
        preset = _preset_match(title)
        ruleset = _slug(preset.get("ruleset") if preset else title)
        out.append(
            {
                "id": _slug(title),
                "name": title,
                "source": "duckduckgo",
                "summary": summary,
                "beginner_hint": (
                    str(preset.get("beginner_hint"))
                    if preset and preset.get("beginner_hint")
                    else "Use this as a quick orientation, then keep rulings concise and check your active rulebook snippets."
                ),
                "ruleset": ruleset,
                "setup_system": str(preset.get("setup_system") if preset else "generic"),
                "url": str(item.get("url") or ""),
            }
        )
    return out


def _game_system_relevance_score(item: dict[str, Any], query: str) -> float:
    text = " ".join(
        [
            str(item.get("name") or ""),
            str(item.get("summary") or ""),
            str(item.get("beginner_hint") or ""),
            str(item.get("ruleset") or ""),
        ]
    ).lower()
    q = str(query or "").strip().lower()
    score = 0.0

    if "tabletop" in text:
        score += 2.5
    if "role-playing" in text or "rpg" in text:
        score += 2.5
    if "game" in text and "system" in text:
        score += 1.5

    for tok in [t for t in q.split() if t]:
        if tok in text:
            score += 1.2

    src = str(item.get("source") or "").strip().lower()
    if src == "preset":
        score += 3.0
    elif src == "wikipedia":
        score += 1.0
    elif src == "duckduckgo":
        score += 0.8

    setup = str(item.get("setup_system") or "").strip().lower()
    if setup in {"numenera", "dnd5e", "pf2e"}:
        score += 1.2
    if str(item.get("url") or "").strip():
        score += 0.4

    return score


@dataclass
class ControlProcessor(FrameProcessor):  # type: ignore[misc]
    """Handle UI control messages (settings, knowledge uploads/ingest, memory).

    Control frames are swallowed (not forwarded to STT), while responses are emitted as
    OutputTransportMessageUrgentFrame to reach the websocket output transport.
    """

    settings_store: SettingsStore
    state: WorldStateStore
    knowledge: KnowledgeManager
    controller: RLMController | None = None
    barge_in_state: Any | None = None

    session_id: str = "control"

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    # Voice diagnostics (best-effort, helps debug "PTT/VAD but no response" issues).
    _utt_active: bool = field(default=False, init=False)
    _utt_started_at: float | None = field(default=None, init=False)
    _utt_audio_frames: int = field(default=0, init=False)
    _utt_audio_bytes: int = field(default=0, init=False)
    _utt_audio_sample_rate: int = field(default=16000, init=False)
    _utt_voice_pcm: array.array = field(default_factory=lambda: array.array("h"), init=False)
    _utt_voice_crossings: int = field(default=0, init=False)
    _utt_voice_edges: int = field(default=0, init=False)
    _utt_voice_last_sign: int = field(default=0, init=False)
    _barge_in_active: bool = field(default=False, init=False)
    _last_barge_in_at: float = field(default=0.0, init=False)
    _llm_timeout_secs: float = 45.0
    _prompt_rpc_soft_timeout_secs: float = 12.0
    _voiceprint_sample_cap: int = 32000

    def _dbg(self, msg: str) -> None:
        if os.environ.get("GM_CONTROL_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
            print(f"[control] {msg}", flush=True)

    def _is_gm_speaking_for_barge_in(self, frame: Any) -> bool:
        # Primary signal: server-side bot-speaking tracker shared from downstream pipeline.
        state_signal: bool | None = None
        if self.barge_in_state is not None:
            try:
                state_signal = bool(getattr(self.barge_in_state, "gm_speaking", False))
            except Exception:
                state_signal = False

        # Fallback signal: client-side playback state hint in VAD message.
        hint_signal: bool | None = None
        try:
            hint = getattr(frame, "gm_speaking", None)
            if hint is not None:
                hint_signal = bool(hint)
        except Exception:
            hint_signal = None

        # Trust either signal if present; preserve legacy behavior otherwise.
        if state_signal is None and hint_signal is None:
            return True
        return bool(state_signal) or bool(hint_signal)

    def __post_init__(self) -> None:
        if FrameDirection is None:  # pragma: no cover
            raise RuntimeError(
                "Pipecat is not installed. Install with a Python <3.14 env: pip install -e '.[voice]'"
            )
        super().__init__(name="ControlProcessor")

    def _reset_utterance_voice_metrics(self) -> None:
        self._utt_audio_sample_rate = 16000
        self._utt_voice_pcm = array.array("h")
        self._utt_voice_crossings = 0
        self._utt_voice_edges = 0
        self._utt_voice_last_sign = 0

    def _append_utterance_audio(self, frame: Any) -> None:
        raw = getattr(frame, "audio", None)
        if not raw:
            return
        try:
            audio = bytes(raw)
        except Exception:
            return
        if len(audio) < 2:
            return
        if len(audio) % 2:
            audio = audio[:-1]
        if not audio:
            return

        try:
            sample_rate = int(getattr(frame, "sample_rate", 16000) or 16000)
            if sample_rate > 0:
                self._utt_audio_sample_rate = sample_rate
        except Exception:
            pass

        chunk = array.array("h")
        try:
            chunk.frombytes(audio)
        except Exception:
            return
        if not chunk:
            return

        remain = int(self._voiceprint_sample_cap) - len(self._utt_voice_pcm)
        if remain > 0:
            self._utt_voice_pcm.extend(chunk[:remain])

        # Dead-zone sign tracking to make ZCR less sensitive to low-level noise.
        last_sign = int(self._utt_voice_last_sign)
        for s in chunk:
            if s > 80:
                sign = 1
            elif s < -80:
                sign = -1
            else:
                continue
            if last_sign != 0:
                self._utt_voice_edges += 1
                if sign != last_sign:
                    self._utt_voice_crossings += 1
            last_sign = sign
        self._utt_voice_last_sign = last_sign

    def _estimate_utterance_pitch_hz(self, *, sample_rate: int) -> float:
        pcm = self._utt_voice_pcm
        n = len(pcm)
        if n < 512 or sample_rate <= 0:
            return 0.0

        # Pick the highest-energy short window; this usually lands on voiced speech.
        win = min(n, max(1024, int(sample_rate * 0.16)))
        if win < 256:
            return 0.0
        step = max(160, win // 4)
        best_i = 0
        best_e = 0.0
        i = 0
        while i + win <= n:
            e = 0.0
            for j in range(i, i + win):
                x = float(pcm[j])
                e += x * x
            if e > best_e:
                best_e = e
                best_i = i
            i += step
        if best_e <= 1.0:
            return 0.0

        seg = [float(pcm[j]) for j in range(best_i, best_i + win)]
        mean = sum(seg) / max(1, len(seg))
        seg = [x - mean for x in seg]
        rms = math.sqrt(sum((x * x) for x in seg) / max(1, len(seg)))
        if rms < 120.0:
            return 0.0

        # Center clipping improves pitch robustness on noisy utterances.
        clip = max(60.0, rms * 0.25)
        clipped: list[float] = []
        for x in seg:
            if x > clip:
                clipped.append(x - clip)
            elif x < -clip:
                clipped.append(x + clip)
            else:
                clipped.append(0.0)

        min_lag = max(20, int(sample_rate / 350))
        max_lag = min(len(clipped) - 2, int(sample_rate / 70))
        if max_lag <= min_lag:
            return 0.0

        best_lag = 0
        best_score = 0.0
        max_i = len(clipped)
        for lag in range(min_lag, max_lag + 1):
            num = 0.0
            den_a = 0.0
            den_b = 0.0
            stop = max_i - lag
            for idx in range(stop):
                a = clipped[idx]
                b = clipped[idx + lag]
                num += a * b
                den_a += a * a
                den_b += b * b
            if den_a <= 1e-6 or den_b <= 1e-6:
                continue
            score = num / math.sqrt(den_a * den_b)
            if score > best_score:
                best_score = score
                best_lag = lag

        if best_lag <= 0 or best_score < 0.28:
            return 0.0
        pitch = float(sample_rate) / float(best_lag)
        if not (60.0 <= pitch <= 420.0):
            return 0.0
        return pitch

    def _build_server_voiceprint(self) -> dict[str, float] | None:
        zcr = 0.0
        if self._utt_voice_edges > 0:
            zcr = float(self._utt_voice_crossings) / float(self._utt_voice_edges)
            zcr = max(0.0, min(1.0, zcr))
        pitch = self._estimate_utterance_pitch_hz(sample_rate=int(self._utt_audio_sample_rate or 16000))
        if pitch <= 0.0 and self._utt_voice_edges <= 0:
            return None
        return {"pitch_hz": round(pitch, 2), "zcr": round(zcr, 4)}

    async def _sync_player_profiles_to_state(self, settings: Any) -> None:
        try:
            profiles = _voice_profiles_from_settings(settings)
            if not profiles:
                return
            await self.state.upsert_player_profiles(str(settings.voice.campaign_id), profiles)
        except Exception:
            # Best-effort sync; gameplay/settings flow should not fail because of this.
            return

    async def _hydrate_player_profiles_from_state(self, settings: Any) -> Any:
        try:
            campaign_id = str(settings.voice.campaign_id)
            rows = await self.state.list_player_profiles(campaign_id)
        except Exception:
            rows = []
        if not rows:
            return settings

        ids = [str(r.get("player_id") or "").strip() for r in rows if str(r.get("player_id") or "").strip()]
        if not ids:
            return settings
        active = str(getattr(settings.voice, "active_player_id", "") or getattr(settings.voice, "player_id", "")).strip()
        if active not in ids:
            active = ids[0]

        return self.settings_store.update(
            {
                "voice": {
                    "player_profiles": rows,
                    "active_player_id": active,
                    "player_id": active,
                }
            }
        )

    async def process_frame(self, frame, direction):  # Frame, FrameDirection
        await super().process_frame(frame, direction)

        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        # Track basic audio/VAD health so we can emit helpful errors when the user
        # "talks" but the server never receives audio frames.
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._utt_active = True
            self._utt_started_at = time.perf_counter()
            self._utt_audio_frames = 0
            self._utt_audio_bytes = 0
            self._reset_utterance_voice_metrics()
            # Explicitly request interruption on player speech. In this websocket+manual-VAD
            # setup, VAD start frames don't always auto-create interruption frames upstream.
            now = time.perf_counter()
            allow_barge_in = bool(getattr(self, "_allow_interruptions", True))
            gm_speaking = self._is_gm_speaking_for_barge_in(frame)
            if (
                allow_barge_in
                and gm_speaking
                and not self._barge_in_active
                and (now - self._last_barge_in_at) >= 0.12
            ):
                self._barge_in_active = True
                try:
                    await self.push_interruption_task_frame_and_wait(timeout=1.0)
                    self._last_barge_in_at = time.perf_counter()
                    await self._send({"type": "debug", "event": "barge_in_interrupt_requested"}, direction)
                except Exception as e:
                    await self._send(
                        {
                            "type": "debug",
                            "event": "barge_in_interrupt_failed",
                            "error": str(e),
                        },
                        direction,
                    )
                finally:
                    self._barge_in_active = False
        elif isinstance(frame, InputAudioRawFrame):
            if self._utt_active:
                self._utt_audio_frames += 1
                try:
                    self._utt_audio_bytes += len(frame.audio or b"")
                except Exception:
                    pass
                self._append_utterance_audio(frame)
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            if self._utt_active:
                dt_ms = None
                if self._utt_started_at is not None:
                    dt_ms = int((time.perf_counter() - self._utt_started_at) * 1000)

                client_vp = getattr(frame, "voiceprint", None)
                server_vp = self._build_server_voiceprint()
                vp_source = "none"
                vp = None
                if isinstance(client_vp, dict):
                    vp = client_vp
                    vp_source = "client"
                if isinstance(server_vp, dict):
                    vp = server_vp
                    vp_source = "server_audio"
                    try:
                        setattr(frame, "voiceprint", server_vp)
                    except Exception:
                        pass

                # Server log (helps when UI just says "GM not answering").
                print(
                    "voice_in: vad_stop "
                    f"frames={self._utt_audio_frames} bytes={self._utt_audio_bytes}"
                    + (f" dt_ms={dt_ms}" if dt_ms is not None else "")
                )

                # If we never received any audio frames, STT cannot transcribe.
                if self._utt_audio_frames <= 0:
                    await self._send(
                        {
                            "type": "error",
                            "error": (
                                "No audio was received for this utterance. "
                                "If you're on a LAN IP (http://192.168.x.x), most browsers block the microphone. "
                                "Open the UI via http://localhost:8000/ or use HTTPS."
                            ),
                        },
                        direction,
                    )
                else:
                    await self._send(
                        {
                            "type": "debug",
                            "event": "utterance_stats",
                            "frames": self._utt_audio_frames,
                            "bytes": self._utt_audio_bytes,
                            "dt_ms": dt_ms,
                            "voiceprint_source": vp_source,
                            "voiceprint_pitch_hz": (
                                float(vp.get("pitch_hz", 0) or 0) if isinstance(vp, dict) else None
                            ),
                            "voiceprint_zcr": (
                                float(vp.get("zcr", 0) or 0) if isinstance(vp, dict) else None
                            ),
                        },
                        direction,
                    )

                self._utt_active = False
                self._utt_started_at = None
                self._reset_utterance_voice_metrics()

        if isinstance(frame, GMClientMessageFrame):
            async with self._lock:
                await self._handle_message(frame.message, direction)
            return  # swallow

        await self.push_frame(frame, direction)

    async def _send(self, msg: dict[str, Any], direction) -> None:
        await self.push_frame(OutputTransportMessageUrgentFrame(msg), direction)

    async def _reply(self, *, req_id: str | None, payload: dict[str, Any], direction) -> None:
        if req_id is not None:
            payload = dict(payload)
            payload["req_id"] = req_id
        await self._send(payload, direction)

    async def _complete_prompt_llm(self, *, model: str, system: str, user: str, temperature: float) -> str:
        """Run prompt-generation LLM calls off the main event loop.

        We intentionally use a blocking client in a worker thread so the Pipecat
        websocket loop remains responsive even if upstream LLM networking hangs.
        """

        def _run_blocking() -> str:
            try:
                from openai import OpenAI  # type: ignore
            except ModuleNotFoundError as e:  # pragma: no cover
                raise RuntimeError("OpenAI SDK is not installed.") from e

            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("Missing OPENAI_API_KEY.")

            base_url = os.environ.get("OPENAI_BASE_URL") or None
            if base_url is not None and not str(base_url).strip():
                base_url = None

            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=30.0,
                max_retries=1,
            )
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
            )
            msg = resp.choices[0].message.content if resp.choices else None
            return (msg or "").strip()

        return await asyncio.to_thread(_run_blocking)

    async def _complete_prompt_llm_best_effort(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float,
    ) -> str:
        """Return LLM text quickly or fail fast so UI RPCs never hang."""
        task = asyncio.create_task(
            self._complete_prompt_llm(
                model=model,
                system=system,
                user=user,
                temperature=temperature,
            )
        )
        done, _pending = await asyncio.wait({task}, timeout=self._prompt_rpc_soft_timeout_secs)
        if not done:
            task.cancel()
            raise RuntimeError(
                f"Prompt generation timed out after {int(self._prompt_rpc_soft_timeout_secs)}s."
            )
        return await task

    def _fallback_prompt_templates(
        self,
        *,
        style: str,
        basic_prompt: str,
        doc_kinds: list[str],
        rulesets: list[str],
    ) -> dict[str, str]:
        style_hint = style or "neutral heroic fantasy"
        basic_hint = basic_prompt or "be concise and practical"
        kinds_hint = ", ".join(doc_kinds[:6]) if doc_kinds else "rules, lore, adventure"
        ruleset_hint = ", ".join(rulesets[:4]) if rulesets else "default system"

        intent = (
            "Classify the player's utterance into one label only: "
            "action, question, dialogue, explore, social, rules, or meta. "
            "Output only the label."
        )
        resolve_system = (
            f"You are a tabletop RPG Game Master ({style_hint}). "
            f"Apply this guidance: {basic_hint}. "
            "Respond in 1-3 short sentences, concrete and playable."
        )
        resolve_user = (
            "Player: {{transcript}}\n\n"
            "Recent memory:\n{{memory}}\n\n"
            "State:\n{{state_json}}\n\n"
            "Knowledge snippets:\n{{snippets}}\n\n"
            f"Known doc kinds: {kinds_hint}. Known rulesets: {ruleset_hint}.\n"
            "Narrate immediate outcome, include necessary checks/rulings, then ask what the player does next."
        )
        return {
            "intent_classify_system": intent,
            "resolve_system": resolve_system,
            "resolve_user_template": resolve_user,
        }

    async def _search_game_systems(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        n = max(3, min(12, int(limit)))
        local = _local_game_system_search(query, limit=n)
        wiki_task = asyncio.create_task(asyncio.to_thread(_wiki_game_system_search, query, limit=n))
        ddg_task = asyncio.create_task(asyncio.to_thread(_duckduckgo_game_system_search, query, limit=n))
        web: list[dict[str, Any]] = []
        ddg: list[dict[str, Any]] = []
        try:
            web, ddg = await asyncio.gather(wiki_task, ddg_task)
        except Exception:
            # Best effort: gather individual task results if available.
            for task_name, task in [("wiki", wiki_task), ("ddg", ddg_task)]:
                try:
                    val = await task
                    if task_name == "wiki":
                        web = val
                    else:
                        ddg = val
                except Exception:
                    continue

        merged_scored: list[tuple[float, dict[str, Any]]] = []
        seen: set[str] = set()
        for item in [*local, *web, *ddg]:
            name_key = _slug(str(item.get("name") or ""), fallback="")
            if not name_key or name_key in seen:
                continue
            seen.add(name_key)
            score = _game_system_relevance_score(item, query)
            item_copy = dict(item)
            item_copy["score"] = round(score, 3)
            merged_scored.append((score, item_copy))

        merged_scored.sort(
            key=lambda x: (
                -x[0],
                0 if str((x[1] or {}).get("source") or "") == "preset" else 1,
                str((x[1] or {}).get("name") or "").lower(),
            )
        )
        return [item for _score, item in merged_scored[:n]]

    async def _handle_message(self, msg: dict[str, Any], direction) -> None:
        t = str(msg.get("type") or "").strip()
        req_id = msg.get("req_id")
        if req_id is not None:
            req_id = str(req_id)
        self._dbg(f"rx type={t} req_id={req_id or '-'}")

        try:
            if t == "server_status":
                settings = self.settings_store.get()
                codex_ok, codex_status = CodexChatGPTLLM.login_status()
                await self._reply(
                    req_id=req_id,
                    payload={
                        "type": "server_status",
                        "openai_api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
                        "deepgram_api_key_present": bool(os.environ.get("DEEPGRAM_API_KEY")),
                        "elevenlabs_api_key_present": bool(os.environ.get("ELEVENLABS_API_KEY")),
                        "codex_chatgpt_available": bool(codex_ok),
                        "codex_chatgpt_status": codex_status,
                        "openai_base_url": (os.environ.get("OPENAI_BASE_URL") or ""),
                        "settings": settings.model_dump(),
                    },
                    direction=direction,
                )
                return

            if t == "secrets_get":
                await self._reply(
                    req_id=req_id,
                    payload={
                        "type": "secrets",
                        "secrets": _secrets_payload(),
                    },
                    direction=direction,
                )
                return

            if t == "secrets_update":
                updates_raw = msg.get("updates")
                clear_raw = msg.get("clear_keys")
                if updates_raw is not None and not isinstance(updates_raw, dict):
                    raise RuntimeError("secrets_update: updates must be an object.")
                if clear_raw is not None and not isinstance(clear_raw, list):
                    raise RuntimeError("secrets_update: clear_keys must be a list.")

                updates: dict[str, str | None] = {}
                for k, v in (updates_raw or {}).items():
                    key = str(k or "").strip()
                    if key not in _ENV_SECRET_FIELDS:
                        continue
                    sval = str(v or "").strip()
                    if key == "OPENAI_BASE_URL" and not sval:
                        updates[key] = None
                    elif sval:
                        updates[key] = sval

                for k in (clear_raw or []):
                    key = str(k or "").strip()
                    if key in _ENV_SECRET_FIELDS:
                        updates[key] = None

                if not updates:
                    raise RuntimeError("No valid secret values were provided.")

                await asyncio.to_thread(_apply_env_updates, updates)

                await self._reply(
                    req_id=req_id,
                    payload={
                        "type": "secrets",
                        "secrets": _secrets_payload(),
                        "updated_keys": [k for k, v in updates.items() if v is not None],
                        "cleared_keys": [k for k, v in updates.items() if v is None],
                        "restart_required": True,
                        "restart_keys": ["openai.stt/tts/llm", "deepgram.stt", "elevenlabs.tts"],
                    },
                    direction=direction,
                )
                return

            if t == "settings_get":
                settings = self.settings_store.get()
                await self._reply(req_id=req_id, payload={"type": "settings", "settings": settings.model_dump()}, direction=direction)
                return

            if t == "settings_update":
                patch = msg.get("patch")
                if not isinstance(patch, dict):
                    raise RuntimeError("settings_update requires {patch:{...}}")

                before = self.settings_store.get()
                after = self.settings_store.update(patch)

                # If campaign changed, load campaign-specific player profiles.
                if before.voice.campaign_id != after.voice.campaign_id:
                    after = await self._hydrate_player_profiles_from_state(after)

                # Persist current player profile config in DB for this campaign.
                await self._sync_player_profiles_to_state(after)

                # Best-effort hot-apply for LLM model (others require restart).
                if self.controller is not None and getattr(self.controller.llm, "model", None) is not None:
                    try:
                        self.controller.llm.model = after.openai.llm_model  # type: ignore[attr-defined]
                    except Exception:
                        pass

                restart_keys: list[str] = []
                if before.voice.ws_host != after.voice.ws_host or before.voice.ws_port != after.voice.ws_port:
                    restart_keys.append("voice.ws_host/ws_port")
                if before.voice.http_host != after.voice.http_host or before.voice.http_port != after.voice.http_port:
                    restart_keys.append("voice.http_host/http_port")
                if (
                    before.openai.stt_model != after.openai.stt_model
                    or before.openai.stt_provider != after.openai.stt_provider
                    or before.openai.deepgram_feature_profile != after.openai.deepgram_feature_profile
                ):
                    restart_keys.append("openai.stt_provider/stt_model/deepgram_feature_profile")
                if before.openai.llm_provider != after.openai.llm_provider:
                    restart_keys.append("openai.llm_provider")
                if (
                    before.openai.tts_model != after.openai.tts_model
                    or before.openai.tts_voice != after.openai.tts_voice
                    or before.openai.tts_provider != after.openai.tts_provider
                ):
                    restart_keys.append("openai.tts_provider/tts_model/tts_voice")

                # If primary rulebook settings changed, best-effort sync in background.
                # This keeps the save RPC fast and non-blocking.
                rulebook_changed = _rulebook_sync_signature(before) != _rulebook_sync_signature(after)
                source = str(after.knowledge.primary_rulebook_source or "path").strip().lower()
                if source == "doc":
                    has_rulebook_source = bool(
                        str(after.knowledge.primary_rulebook_doc_choice or after.knowledge.primary_rulebook_doc_id or "").strip()
                    )
                else:
                    has_rulebook_source = bool(str(after.knowledge.primary_rulebook_path or "").strip())
                should_auto_sync = has_rulebook_source and (
                    bool(after.knowledge.primary_rulebook_auto_ingest)
                    or bool(after.knowledge.primary_rulebook_auto_activate)
                )
                if rulebook_changed and should_auto_sync:
                    async def progress_cb(payload: dict[str, Any]) -> None:
                        await self._send(payload, direction)

                    async def run_rulebook_sync() -> None:
                        try:
                            await self.knowledge.sync_primary_rulebook(progress_cb=progress_cb)
                            await self._send(
                                {"type": "settings", "settings": self.settings_store.get().model_dump()},
                                direction,
                            )
                        except Exception as e:
                            await self._send(
                                {"type": "kb_rulebook_sync_status", "status": "error", "error": str(e)},
                                direction,
                            )

                    asyncio.create_task(run_rulebook_sync())

                await self._reply(
                    req_id=req_id,
                    payload={
                        "type": "settings",
                        "settings": after.model_dump(),
                        "restart_required": bool(restart_keys),
                        "restart_keys": restart_keys,
                    },
                    direction=direction,
                )
                return

            if t == "kb_sync_rulebook":
                ingest = _optional_bool(msg.get("ingest"))
                activate = _optional_bool(msg.get("activate"))

                async def progress_cb(payload: dict[str, Any]) -> None:
                    await self._send(payload, direction)

                async def run_rulebook_sync() -> None:
                    try:
                        await self.knowledge.sync_primary_rulebook(
                            progress_cb=progress_cb,
                            ingest=ingest,
                            activate=activate,
                        )
                        await self._send(
                            {"type": "settings", "settings": self.settings_store.get().model_dump()},
                            direction,
                        )
                    except Exception as e:
                        await self._send(
                            {"type": "kb_rulebook_sync_status", "status": "error", "error": str(e)},
                            direction,
                        )

                asyncio.create_task(run_rulebook_sync())
                await self._reply(req_id=req_id, payload={"type": "kb_rulebook_sync_started"}, direction=direction)
                return

            if t == "prompts_generate":
                self._dbg("prompts_generate: begin")
                if self.controller is None:
                    raise RuntimeError("Prompt generation is not available.")

                style = msg.get("style")
                style = str(style).strip() if style is not None and str(style).strip() else ""

                basic_prompt = msg.get("basic_prompt")
                basic_prompt = str(basic_prompt).strip() if basic_prompt is not None and str(basic_prompt).strip() else ""

                settings = self.settings_store.get()
                docs = await self.knowledge.list_documents()
                doc_kinds = sorted({str(d.get("doc_kind") or "") for d in docs if isinstance(d, dict)} - {""})
                rulesets = sorted({str(d.get("ruleset") or "") for d in docs if isinstance(d, dict)} - {""})
                llm_model = str(settings.openai.llm_model or "gpt-4.1-mini")
                self._dbg(
                    "prompts_generate: docs="
                    + str(len(docs))
                    + " kinds="
                    + str(doc_kinds)
                    + " rulesets="
                    + str(rulesets)
                    + " model="
                    + llm_model
                )

                system = (
                    "You are an expert prompt engineer for a voice tabletop RPG Game Master. "
                    "Return ONLY valid JSON (no markdown, no code fences). "
                    "The JSON object MUST include these string keys: "
                    "intent_classify_system, resolve_system, resolve_user_template. "
                    "The resolve_user_template MUST use these variables exactly: "
                    "{{transcript}}, {{memory}}, {{state_json}}, {{snippets}}. "
                    "Keep the GM concise and actionable (1-3 short sentences)."
                )
                user = (
                    f"Style/genre: {style or '(default)'}\n"
                    f"User basic prompt: {basic_prompt or '(none)'}\n"
                    f"Knowledge enabled: {bool(settings.knowledge.enabled)}\n"
                    f"Known doc_kinds: {doc_kinds or '(none)'}\n"
                    f"Known rulesets: {rulesets or '(none)'}\n\n"
                    "Generate prompt templates suitable for:\n"
                    "- classifying player intent\n"
                    "- resolving a turn into GM narration\n"
                    "- using knowledge snippets when available\n"
                    "- using memory when enabled\n"
                )

                # IMPORTANT: do the LLM call in a background task so control RPCs never
                # block the audio/VAD frames flowing to STT (voice responsiveness).
                await self._send({"type": "prompts_generate_started"}, direction)
                self._dbg("prompts_generate: started_event_sent")
                try:
                    out = await self._complete_prompt_llm_best_effort(
                        model=llm_model,
                        system=system,
                        user=user,
                        temperature=0.2,
                    )
                    self._dbg(f"prompts_generate: llm_returned chars={len(out)}")
                    obj = _extract_json_obj(out)
                    self._dbg("prompts_generate: parsed_json_ok")

                    patch: dict[str, Any] = {"prompts": {}}
                    self._dbg("prompts_generate: building_patch")
                    for k in ["intent_classify_system", "resolve_system", "resolve_user_template"]:
                        v = obj.get(k)
                        if isinstance(v, str) and v.strip():
                            patch["prompts"][k] = v
                    self._dbg(f"prompts_generate: base_patch_keys={list((patch.get('prompts') or {}).keys())}")

                    # Optional extras.
                    if isinstance(obj.get("include_memory"), bool):
                        patch["prompts"]["include_memory"] = bool(obj["include_memory"])
                    if obj.get("memory_turns") is not None:
                        try:
                            patch["prompts"]["memory_turns"] = max(0, int(obj["memory_turns"]))
                        except Exception:
                            pass
                    self._dbg("prompts_generate: optional_patch_done")

                except Exception as e:
                    self._dbg(f"prompts_generate: llm_failed fallback={e}")
                    patch = {"prompts": self._fallback_prompt_templates(
                        style=style,
                        basic_prompt=basic_prompt,
                        doc_kinds=doc_kinds,
                        rulesets=rulesets,
                    )}
                    patch["prompts"]["_generator_note"] = f"fallback: {e}"
                if not patch["prompts"]:
                    self._dbg("prompts_generate: empty_patch_using_fallback")
                    patch = {"prompts": self._fallback_prompt_templates(
                        style=style,
                        basic_prompt=basic_prompt,
                        doc_kinds=doc_kinds,
                        rulesets=rulesets,
                    )}
                self._dbg("prompts_generate: before_settings_update")

                after = self.settings_store.update(patch)
                self._dbg("prompts_generate: settings_updated")
                await self._reply(
                    req_id=req_id,
                    payload={
                        "type": "settings",
                        "settings": after.model_dump(),
                        "restart_required": False,
                        "restart_keys": [],
                    },
                    direction=direction,
                )
                self._dbg("prompts_generate: reply_sent")
                return

            if t == "prompt_generate":
                if self.controller is None:
                    raise RuntimeError("Prompt generation is not available.")

                field_name = str(msg.get("field") or "").strip()
                allowed_fields = {"intent_classify_system", "resolve_system", "resolve_user_template"}
                if field_name not in allowed_fields:
                    raise RuntimeError(f"Unknown prompt field: {field_name!r}")

                style = msg.get("style")
                style = str(style).strip() if style is not None and str(style).strip() else ""

                basic_prompt = msg.get("basic_prompt")
                basic_prompt = str(basic_prompt).strip() if basic_prompt is not None and str(basic_prompt).strip() else ""

                settings = self.settings_store.get()
                docs = await self.knowledge.list_documents()
                doc_kinds = sorted({str(d.get("doc_kind") or "") for d in docs if isinstance(d, dict)} - {""})
                rulesets = sorted({str(d.get("ruleset") or "") for d in docs if isinstance(d, dict)} - {""})
                llm_model = str(settings.openai.llm_model or "gpt-4.1-mini")

                current_value = ""
                try:
                    current_value = str(getattr(settings.prompts, field_name) or "").strip()
                except Exception:
                    current_value = ""

                # Field-specific constraints.
                field_desc = {
                    "intent_classify_system": (
                        "Write a SYSTEM prompt for intent classification. "
                        "Output must be a single short intent label (no JSON, no explanation)."
                    ),
                    "resolve_system": (
                        "Write a SYSTEM prompt for resolving a turn as the GM. "
                        "Be concise (1-3 short sentences) and deterministic."
                    ),
                    "resolve_user_template": (
                        "Write a USER TEMPLATE for resolving a turn. "
                        "It MUST use these variables exactly: {{transcript}}, {{memory}}, {{state_json}}, {{snippets}}."
                    ),
                }[field_name]

                system = (
                    "You are an expert prompt engineer for a voice tabletop RPG Game Master.\n"
                    "Return ONLY valid JSON (no markdown, no code fences).\n"
                    "The JSON object MUST include a string key named 'value'.\n"
                    f"The value must satisfy: {field_desc}\n"
                )
                user = (
                    f"Target field: {field_name}\n"
                    f"Style/genre: {style or '(default)'}\n"
                    f"User basic prompt: {basic_prompt or '(none)'}\n"
                    f"Knowledge enabled: {bool(settings.knowledge.enabled)}\n"
                    f"Known doc_kinds: {doc_kinds or '(none)'}\n"
                    f"Known rulesets: {rulesets or '(none)'}\n\n"
                    "If a current value is provided, you may improve it (do not merely rephrase).\n"
                    f"Current value:\n{current_value or '(empty)'}\n"
                )

                await self._send({"type": "prompt_generate_started", "field": field_name}, direction)
                try:
                    out = await self._complete_prompt_llm_best_effort(
                        model=llm_model,
                        system=system,
                        user=user,
                        temperature=0.2,
                    )
                    obj = _extract_json_obj(out)
                    v = obj.get("value")
                    if not isinstance(v, str) or not v.strip():
                        raise RuntimeError("LLM did not return a non-empty 'value' field.")

                    patch = {"prompts": {field_name: v}}
                    after = self.settings_store.update(patch)
                    await self._reply(
                        req_id=req_id,
                        payload={
                            "type": "settings",
                            "settings": after.model_dump(),
                            "restart_required": False,
                            "restart_keys": [],
                            "prompt_generated": {"field": field_name},
                        },
                        direction=direction,
                    )
                except Exception as e:
                    fallback = self._fallback_prompt_templates(
                        style=style,
                        basic_prompt=basic_prompt,
                        doc_kinds=doc_kinds,
                        rulesets=rulesets,
                    )
                    patch = {"prompts": {field_name: fallback.get(field_name, "")}}
                    after = self.settings_store.update(patch)
                    await self._reply(
                        req_id=req_id,
                        payload={
                            "type": "settings",
                            "settings": after.model_dump(),
                            "restart_required": False,
                            "restart_keys": [],
                            "prompt_generated": {"field": field_name, "fallback": True, "note": str(e)},
                        },
                        direction=direction,
                    )
                return

            if t == "campaign_new":
                # Create a fresh campaign id and switch settings to it.
                settings = self.settings_store.get()
                new_id = uuid.uuid4().hex
                name = msg.get("name")
                name_s = str(name).strip() if name is not None and str(name).strip() else None

                after = self.settings_store.update({"voice": {"campaign_id": new_id}})
                # Ensure row exists immediately (useful for "recent saves" UX).
                ctx = _ctx_from_settings(after, session_id=self.session_id)
                await self.state.ensure_campaign(ctx, name=name_s)
                await self._sync_player_profiles_to_state(after)

                await self._reply(
                    req_id=req_id,
                    payload={
                        "type": "settings",
                        "settings": after.model_dump(),
                        "restart_required": False,
                        "restart_keys": [],
                        "campaign": {"id": new_id, "name": name_s or f"Campaign {new_id}"},
                    },
                    direction=direction,
                )
                return

            if t == "campaign_resume_latest":
                latest = await self.state.latest_campaign_id()
                if not latest:
                    raise RuntimeError("No saved campaigns found yet.")

                after = self.settings_store.update({"voice": {"campaign_id": latest}})
                after = await self._hydrate_player_profiles_from_state(after)
                await self._sync_player_profiles_to_state(after)
                await self._reply(
                    req_id=req_id,
                    payload={
                        "type": "settings",
                        "settings": after.model_dump(),
                        "restart_required": False,
                        "restart_keys": [],
                        "campaign": {"id": latest},
                    },
                    direction=direction,
                )
                return

            if t == "campaign_reset":
                settings = self.settings_store.get()
                ctx = _ctx_from_settings(settings, session_id=self.session_id)
                cleared_log = await self.state.clear_interaction_log(ctx)
                cleared_events = await self.state.clear_delayed_events(ctx)
                await self._reply(
                    req_id=req_id,
                    payload={
                        "type": "campaign_reset",
                        "campaign_id": ctx.campaign_id,
                        "cleared_memory_entries": cleared_log,
                        "cleared_delayed_events": cleared_events,
                    },
                    direction=direction,
                )
                return

            if t == "kb_list":
                docs = await self.knowledge.list_documents()
                await self._reply(req_id=req_id, payload={"type": "kb_list", "documents": docs}, direction=direction)
                return

            if t == "setup_system_search":
                q = str(msg.get("query") or "").strip()
                limit = int(msg.get("limit") or 8)
                results = await self._search_game_systems(q, limit=limit)
                await self._reply(
                    req_id=req_id,
                    payload={"type": "setup_system_search_results", "query": q, "results": results},
                    direction=direction,
                )
                return

            if t == "kb_search":
                q = str(msg.get("query") or "").strip()
                if not q:
                    raise RuntimeError("kb_search requires query")
                settings = self.settings_store.get()
                top_k = int(msg.get("top_k") or settings.knowledge.top_k or 5)
                top_k = max(1, min(12, top_k))

                if not getattr(self.knowledge, "qdrant", None):
                    raise RuntimeError("Knowledge backend is not enabled (Qdrant not configured).")

                chunk_type = msg.get("chunk_type")
                chunk_type = str(chunk_type).strip() if chunk_type is not None and str(chunk_type).strip() else ""
                doc_kind = msg.get("doc_kind")
                doc_kind = str(doc_kind).strip() if doc_kind is not None and str(doc_kind).strip() else ""
                collection_target = msg.get("collection_target")
                collection_target = (
                    str(collection_target).strip() if collection_target is not None and str(collection_target).strip() else ""
                )

                filters: dict[str, Any] = {}
                if chunk_type and chunk_type != "any":
                    filters["type"] = chunk_type
                if doc_kind and doc_kind != "any":
                    filters["doc_kind"] = doc_kind
                if collection_target and collection_target != "any":
                    filters["collection_target"] = collection_target
                if settings.knowledge.active_doc_ids:
                    # Respect the UI-selected active docs by default.
                    filters["doc_id"] = list(settings.knowledge.active_doc_ids)

                ctx = _ctx_from_settings(settings, session_id=self.session_id)
                res = await self.knowledge.search(
                    ctx,
                    RetrievalSpec(query=q, top_k=top_k, filters=filters or None),
                )
                await self._reply(
                    req_id=req_id,
                    payload={"type": "kb_search_results", "query": q, "results": res},
                    direction=direction,
                )
                return

            if t == "kb_upload_start":
                filename = str(msg.get("filename") or "upload.pdf")
                doc_id = str(msg.get("doc_id") or "").strip() or __import__("uuid").uuid4().hex
                ruleset = msg.get("ruleset")
                ruleset = str(ruleset).strip() if ruleset is not None and str(ruleset).strip() else None
                doc_kind = msg.get("doc_kind")
                doc_kind = str(doc_kind).strip() if doc_kind is not None and str(doc_kind).strip() else None
                collection_target = msg.get("collection_target")
                collection_target = (
                    str(collection_target).strip()
                    if collection_target is not None and str(collection_target).strip()
                    else None
                )
                total_bytes = msg.get("total_bytes")
                total_bytes_i = int(total_bytes) if total_bytes is not None else None

                upload_id, final_doc_id = await self.knowledge.begin_upload(
                    filename=filename,
                    doc_id=doc_id,
                    ruleset=ruleset,
                    doc_kind=doc_kind,
                    collection_target=collection_target,
                    total_bytes=total_bytes_i,
                )
                await self._reply(
                    req_id=req_id,
                    payload={"type": "kb_upload_started", "upload_id": upload_id, "doc_id": final_doc_id},
                    direction=direction,
                )
                return

            if t == "kb_upload_chunk":
                upload_id = str(msg.get("upload_id") or "")
                seq = int(msg.get("seq") or 0)
                data_b64 = str(msg.get("data_b64") or "")
                res = await self.knowledge.upload_chunk(upload_id=upload_id, seq=seq, data_b64=data_b64)
                await self._reply(req_id=req_id, payload={"type": "kb_upload_chunk_ack", **res}, direction=direction)
                return

            if t == "kb_upload_finish":
                upload_id = str(msg.get("upload_id") or "")
                res = await self.knowledge.finish_upload(upload_id=upload_id)
                await self._reply(req_id=req_id, payload={"type": "kb_upload_finished", **res}, direction=direction)
                return

            if t == "kb_ingest_start":
                doc_id = str(msg.get("doc_id") or "").strip()
                if not doc_id:
                    raise RuntimeError("kb_ingest_start requires doc_id")
                replace_existing = bool(msg.get("replace_existing", True))
                chunk_max_chars = msg.get("chunk_max_chars")
                chunk_overlap = msg.get("chunk_overlap")
                ruleset = msg.get("ruleset")
                ruleset = str(ruleset).strip() if ruleset is not None and str(ruleset).strip() else None

                async def progress_cb(payload: dict[str, Any]) -> None:
                    await self._send(payload, direction)

                async def run_ingest() -> None:
                    try:
                        await self.knowledge.ingest_doc(
                            doc_id=doc_id,
                            progress_cb=progress_cb,
                            chunk_max_chars=int(chunk_max_chars) if chunk_max_chars is not None else None,
                            chunk_overlap=int(chunk_overlap) if chunk_overlap is not None else None,
                            ruleset=ruleset,
                            replace_existing=replace_existing,
                        )
                    except Exception as e:
                        # ingest_doc already emits kb_ingest_status(error) and updates the index.
                        await self._send({"type": "error", "error": f"KB ingest failed: {e}"}, direction)

                asyncio.create_task(run_ingest())
                await self._reply(req_id=req_id, payload={"type": "kb_ingest_started", "doc_id": doc_id}, direction=direction)
                return

            if t == "kb_delete":
                doc_id = str(msg.get("doc_id") or "").strip()
                if not doc_id:
                    raise RuntimeError("kb_delete requires doc_id")
                delete_file = bool(msg.get("delete_file", False))
                await self.knowledge.delete_doc(doc_id=doc_id, delete_file=delete_file)
                await self._reply(req_id=req_id, payload={"type": "kb_deleted", "doc_id": doc_id}, direction=direction)
                return

            if t == "memory_get":
                settings = self.settings_store.get()
                limit = int(msg.get("limit") or 50)
                scope = str(msg.get("scope") or "campaign").strip().lower()
                if scope not in {"campaign", "session", "player"}:
                    scope = "campaign"
                target_session = str(msg.get("session_id") or settings.voice.session_id).strip()
                target_player = str(msg.get("player_id") or settings.voice.player_id).strip()
                ctx = _ctx_from_settings(settings, session_id=self.session_id)
                read_params: dict[str, Any] = {"limit": limit}
                if scope == "session" and target_session:
                    read_params["session_id"] = target_session
                if scope == "player" and target_player:
                    read_params["player_id"] = target_player
                view = await self.state.read(ctx, [StateReadSpec(kind="interaction_log", params=read_params)])
                await self._reply(
                    req_id=req_id,
                    payload={
                        "type": "memory",
                        "campaign_id": ctx.campaign_id,
                        "scope": scope,
                        "session_id": target_session,
                        "player_id": target_player,
                        "entries": list(view.get("interaction_log") or []),
                    },
                    direction=direction,
                )
                return

            if t == "memory_clear":
                settings = self.settings_store.get()
                scope = str(msg.get("scope") or "campaign").strip().lower()
                if scope not in {"campaign", "session", "player"}:
                    scope = "campaign"
                target_session = str(msg.get("session_id") or settings.voice.session_id).strip()
                target_player = str(msg.get("player_id") or settings.voice.player_id).strip()
                ctx = _ctx_from_settings(settings, session_id=self.session_id)
                if scope == "campaign":
                    cleared = await self.state.clear_interaction_log(ctx)
                elif scope == "session":
                    cleared = await self.state.clear_interaction_log_filtered(ctx, session_id=target_session)
                else:
                    cleared = await self.state.clear_interaction_log_filtered(ctx, player_id=target_player)
                await self._reply(
                    req_id=req_id,
                    payload={
                        "type": "memory_cleared",
                        "campaign_id": ctx.campaign_id,
                        "scope": scope,
                        "session_id": target_session,
                        "player_id": target_player,
                        "cleared": cleared,
                    },
                    direction=direction,
                )
                return

            raise RuntimeError(f"unknown control message type: {t!r}")
        except Exception as e:
            # Always return an error payload to the UI.
            await self._reply(req_id=req_id, payload={"type": "error", "error": str(e)}, direction=direction)
