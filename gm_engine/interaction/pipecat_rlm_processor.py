from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

from gm_engine.app.settings_store import SettingsStore
from gm_engine.rlm.controller import RLMController
from gm_engine.rlm.types import TurnContext

try:
    from pipecat.frames.frames import (
        ErrorFrame,
        InterimTranscriptionFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        OutputTransportMessageUrgentFrame,
        TextFrame,
        TranscriptionFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
except ModuleNotFoundError:  # pragma: no cover
    FrameProcessor = object  # type: ignore[assignment,misc]
    FrameDirection = None  # type: ignore[assignment]
    InterimTranscriptionFrame = object  # type: ignore[assignment]
    LLMFullResponseEndFrame = object  # type: ignore[assignment]
    LLMFullResponseStartFrame = object  # type: ignore[assignment]
    OutputTransportMessageUrgentFrame = object  # type: ignore[assignment]
    TextFrame = object  # type: ignore[assignment]
    TranscriptionFrame = object  # type: ignore[assignment]
    VADUserStartedSpeakingFrame = object  # type: ignore[assignment]
    VADUserStoppedSpeakingFrame = object  # type: ignore[assignment]
    ErrorFrame = object  # type: ignore[assignment]


@dataclass
class RLMProcessor(FrameProcessor):  # type: ignore[misc]
    """Pipecat processor: transcription -> RLMController -> narration text frames."""

    controller: RLMController
    settings_store: SettingsStore | None = None

    # Fallbacks when settings_store is not provided.
    campaign_id: str = "demo"
    session_id: str = "voice"
    player_id: str = "player1"
    locale: str = "en-US"

    _turn_counter: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _speaker_to_player: dict[str, str] = field(default_factory=dict, init=False)
    _speaker_scope_key: tuple[str, str] | None = field(default=None, init=False)
    _vad_active: bool = field(default=False, init=False)
    _pending_campaign_id: str = field(default="", init=False)
    _pending_session_id: str = field(default="", init=False)
    _pending_player_id: str = field(default="", init=False)
    _pending_locale: str = field(default="", init=False)
    _pending_transcript_text: str = field(default="", init=False)
    _pending_language_source: str = field(default="", init=False)
    _pending_seen_finalized: bool = field(default=False, init=False)
    _vad_voiceprint_queue: list[dict[str, float]] = field(default_factory=list, init=False)
    _last_flushed_player_id: str = field(default="", init=False)
    _last_flushed_transcript_text: str = field(default="", init=False)
    _player_voiceprints: dict[str, dict[str, float]] = field(default_factory=dict, init=False)

    _openai_tts_voices: set[str] = field(
        default_factory=lambda: {"alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"},
        init=False,
    )

    def _sanitize_tts_text(self, text: str) -> str:
        s = str(text or "")
        if not s.strip():
            return ""

        # Remove common markdown/citation artifacts that degrade TTS quality.
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", s)
        s = re.sub(r"\[(?:rulebook|source|citation)[^\]]*\]", "", s, flags=re.IGNORECASE)
        s = s.replace("**", "").replace("__", "").replace("`", "")
        s = s.replace("—", " - ").replace("–", " - ")
        s = s.replace("•", "- ").replace("·", " ")
        s = re.sub(r"[ \t]+", " ", s)
        s = re.sub(r"\n{3,}", "\n\n", s)
        s = re.sub(r"\s+\n", "\n", s)
        s = re.sub(r"\n\s+", "\n", s)
        return s.strip()

    def _split_tts_chunks(self, text: str, *, max_chars: int = 260) -> list[str]:
        clean = self._sanitize_tts_text(text)
        if not clean:
            return []

        # Sentence-aware split with safe fallback for long segments.
        pieces = re.split(r"(?<=[.!?])\s+|\n+", clean)
        out: list[str] = []
        cur = ""
        for piece in pieces:
            p = str(piece or "").strip()
            if not p:
                continue
            if len(p) > max_chars:
                if cur:
                    out.append(cur)
                    cur = ""
                # Hard-wrap oversized sentence.
                start = 0
                while start < len(p):
                    part = p[start : start + max_chars].strip()
                    if part:
                        out.append(part)
                    start += max_chars
                continue
            if not cur:
                cur = p
                continue
            cand = f"{cur} {p}"
            if len(cand) <= max_chars:
                cur = cand
            else:
                out.append(cur)
                cur = p
        if cur:
            out.append(cur)
        return out

    async def _push_tts_text(self, text: str, *, direction) -> None:
        chunks = self._split_tts_chunks(text)
        if not chunks:
            raw = str(text or "").strip()
            if raw:
                await self.push_frame(TextFrame(raw), direction)
            return
        for chunk in chunks:
            await self.push_frame(TextFrame(chunk), direction)

    def _friendly_error(self, err: str) -> str:
        s = str(err or "").strip()
        if not s:
            s = "Voice pipeline error with empty provider message."
        low = s.lower()
        try:
            if self.settings_store is not None:
                cfg = self.settings_store.get()
                provider = str(cfg.openai.tts_provider or "").strip().lower()
                voice = str(cfg.openai.tts_voice or "").strip()
                if provider == "openai":
                    voice_l = voice.lower()
                    if voice and voice_l not in self._openai_tts_voices:
                        return (
                            f"OpenAI TTS voice is invalid ('{voice}'). "
                            "Use one of: alloy, ash, ballad, coral, echo, sage, shimmer, verse."
                        )
        except Exception:
            pass
        if "connection error" in low and ("stt" in low or "openaisttservice" in low):
            return (
                f"{s} "
                "Check OPENAI_API_KEY / DEEPGRAM_API_KEY, provider base URLs, and outbound internet access."
            )
        if "audio not received" in low:
            return (
                f"{s} "
                "If using browser mic, prefer http://localhost:8000/ or HTTPS; LAN HTTP usually blocks mic."
            )
        return s

    def _frame_language_tag(self, frame: Any) -> str:
        lang = getattr(frame, "language", None)
        if lang is None:
            return ""
        v = getattr(lang, "value", None)
        if v is not None:
            return str(v).strip()
        return str(lang).strip()

    def _normalize_lang_tag(self, tag: str) -> str:
        s = str(tag or "").strip().replace("_", "-")
        return s

    def _lang_base(self, tag: str) -> str:
        s = self._normalize_lang_tag(tag).lower()
        return s.split("-", 1)[0] if s else ""

    def _detect_text_language_tag(self, text: str) -> str:
        t = str(text or "")
        if not t.strip():
            return ""

        # Script-based routing first (reliable for non-Latin).
        cyr = sum(1 for ch in t if "\u0400" <= ch <= "\u04ff")
        han = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
        hira_kata = sum(1 for ch in t if "\u3040" <= ch <= "\u30ff")
        hangul = sum(1 for ch in t if "\uac00" <= ch <= "\ud7af")
        arabic = sum(1 for ch in t if "\u0600" <= ch <= "\u06ff")
        devanagari = sum(1 for ch in t if "\u0900" <= ch <= "\u097f")

        if hangul >= 3:
            return "ko"
        if han >= 3 and hira_kata >= 1:
            return "ja"
        if han >= 3:
            return "zh"
        if arabic >= 3:
            return "ar"
        if devanagari >= 3:
            return "hi"
        if cyr >= 3:
            low = t.lower()
            if any(ch in low for ch in ("і", "ї", "є", "ґ")):
                return "uk"
            return "ru"

        # Lightweight Latin-language hints.
        low = t.lower()
        words = re.findall(r"[a-zA-Z\u00c0-\u024f']+", low)
        if not words:
            return ""
        joined = " " + " ".join(words) + " "

        def has_any(terms: tuple[str, ...]) -> bool:
            return any(f" {w} " in joined for w in terms)

        if has_any(("el", "la", "de", "que", "y", "hola", "gracias", "por", "para")):
            return "es"
        if has_any(("le", "la", "de", "et", "bonjour", "merci", "avec", "pour")):
            return "fr"
        if has_any(("der", "die", "das", "und", "ich", "nicht", "hallo", "danke")):
            return "de"
        if has_any(("olá", "obrigado", "você", "não", "que", "de", "com", "para")):
            return "pt"
        if has_any(("ciao", "grazie", "che", "per", "con", "non", "sono")):
            return "it"

        # Default: English for plain Latin when no other signal.
        return "en"

    def __post_init__(self) -> None:
        if FrameDirection is None:  # pragma: no cover
            raise RuntimeError(
                "Pipecat is not installed. Install with a Python <3.14 env: pip install -e '.[voice]'"
            )
        super().__init__(name="RLMProcessor")

    def _normalize_voiceprint(self, raw: Any) -> dict[str, float] | None:
        if not isinstance(raw, dict):
            return None
        try:
            pitch = float(raw.get("pitch_hz", 0) or 0)
            zcr = float(raw.get("zcr", 0) or 0)
        except Exception:
            return None
        if not (0.0 <= zcr <= 1.0):
            return None
        if pitch != 0 and not (50.0 <= pitch <= 450.0):
            return None
        return {"pitch_hz": pitch, "zcr": zcr}

    def _voiceprint_distance(self, a: dict[str, float], b: dict[str, float]) -> float:
        ap = float(a.get("pitch_hz", 0) or 0)
        bp = float(b.get("pitch_hz", 0) or 0)
        if ap > 0 and bp > 0:
            d_pitch = abs(math.log(ap) - math.log(bp))
        else:
            d_pitch = 0.25
        d_zcr = abs(float(a.get("zcr", 0) or 0) - float(b.get("zcr", 0) or 0))
        return (d_pitch * 0.8) + (d_zcr * 4.0)

    def _pick_player_from_voiceprint(
        self,
        *,
        profiles: list[str],
        candidate: str,
        voiceprint: dict[str, float] | None,
    ) -> str:
        if voiceprint is None or len(profiles) <= 1:
            return candidate

        known = {pid: self._player_voiceprints[pid] for pid in profiles if pid in self._player_voiceprints}
        if not known:
            return candidate

        distances = {pid: self._voiceprint_distance(voiceprint, fp) for pid, fp in known.items()}
        nearest_pid = min(distances, key=distances.get)
        nearest_dist = float(distances[nearest_pid])

        if candidate in known:
            cand_dist = float(distances[candidate])
            # Switch to a different player when the nearest profile is clearly closer.
            if nearest_pid != candidate and (cand_dist - nearest_dist) >= 0.05 and nearest_dist <= 0.72:
                return nearest_pid
        else:
            if nearest_dist < 0.60:
                return nearest_pid

        # If only one known player exists, allow splitting to a new player when voiceprint
        # is far from the known centroid.
        if len(known) == 1:
            only_pid = next(iter(known))
            only_dist = float(distances[only_pid])
            unknown = [p for p in profiles if p not in known]
            if unknown:
                only_fp = known[only_pid]
                only_pitch = float(only_fp.get("pitch_hz", 0) or 0)
                now_pitch = float(voiceprint.get("pitch_hz", 0) or 0)
                pitch_far = False
                if only_pitch > 0 and now_pitch > 0:
                    ratio = max(now_pitch, only_pitch) / max(1e-6, min(now_pitch, only_pitch))
                    pitch_far = ratio >= 1.18
                if only_dist > 0.34 or pitch_far:
                    return unknown[0]

        return candidate

    def _update_player_voiceprint(self, *, player_id: str, voiceprint: dict[str, float] | None) -> None:
        if voiceprint is None:
            return
        pid = str(player_id or "").strip()
        if not pid:
            return
        cur = self._player_voiceprints.get(pid)
        if cur is None:
            self._player_voiceprints[pid] = dict(voiceprint)
            return
        alpha = 0.25
        prev_pitch = float(cur.get("pitch_hz", 0) or 0)
        next_pitch = float(voiceprint.get("pitch_hz", 0) or 0)
        if prev_pitch > 0 and next_pitch > 0:
            cur["pitch_hz"] = (prev_pitch * (1.0 - alpha)) + (next_pitch * alpha)
        elif next_pitch > 0:
            cur["pitch_hz"] = next_pitch
        cur["zcr"] = (float(cur.get("zcr", 0) or 0) * (1.0 - alpha)) + (
            float(voiceprint.get("zcr", 0) or 0) * alpha
        )
        self._player_voiceprints[pid] = cur

    def _resolve_player_id_from_speaker(
        self, *, raw_user_id: str, settings: Any | None, voiceprint: dict[str, float] | None = None
    ) -> tuple[str, bool, str | None]:
        raw = str(raw_user_id or "").strip()
        if settings is None:  # pragma: no cover
            base = raw or self.player_id or "player1"
            return (base, False, None)

        scope = (str(settings.voice.campaign_id), str(settings.voice.session_id))
        if self._speaker_scope_key != scope:
            self._speaker_scope_key = scope
            self._speaker_to_player.clear()
            self._player_voiceprints.clear()

        profiles = []
        for p in list(getattr(settings.voice, "player_profiles", []) or [])[:8]:
            pid = str(getattr(p, "player_id", "") or "").strip()
            if pid:
                profiles.append(pid)
        if not profiles:
            profiles = [str(settings.voice.player_id or "player1").strip() or "player1"]

        active = str(
            getattr(settings.voice, "active_player_id", "")
            or getattr(settings.voice, "player_id", "")
            or profiles[0]
        ).strip()
        if active not in profiles:
            active = profiles[0]

        raw_l = raw.lower()
        diarized_raw = raw_l.startswith("dg_spk_")
        map_key = f"{scope[0]}|{scope[1]}|{raw}" if (raw and diarized_raw) else None
        speaker_mapped = False

        candidate = active
        if raw and raw in profiles:
            candidate = raw
        elif raw and not diarized_raw:
            candidate = active
        elif raw:
            if map_key:
                persisted = (
                    settings.voice.speaker_mappings.get(map_key) if isinstance(settings.voice.speaker_mappings, dict) else None
                )
                persisted = str(persisted or "").strip()
                if persisted and persisted in profiles:
                    self._speaker_to_player[raw] = persisted
                    candidate = persisted
                else:
                    mapped = self._speaker_to_player.get(raw)
                    if mapped and mapped in profiles:
                        candidate = mapped
                    else:
                        used = set(self._speaker_to_player.values())
                        for pid in profiles:
                            if pid not in used:
                                self._speaker_to_player[raw] = pid
                                candidate = pid
                                speaker_mapped = True
                                break
                        else:
                            self._speaker_to_player[raw] = profiles[0]
                            candidate = profiles[0]
                            speaker_mapped = True

        vp_pick = self._pick_player_from_voiceprint(profiles=profiles, candidate=candidate, voiceprint=voiceprint)
        if vp_pick != candidate:
            candidate = vp_pick
            if diarized_raw and raw:
                self._speaker_to_player[raw] = candidate
                speaker_mapped = True

        return (candidate, speaker_mapped, map_key if speaker_mapped else None)

    def _merge_transcript_text(self, previous: str, current: str) -> str:
        prev = str(previous or "").strip()
        cur = str(current or "").strip()
        if not cur:
            return prev
        if not prev:
            return cur
        if cur in prev:
            return prev
        if prev in cur:
            return cur
        return f"{prev} {cur}".strip()

    def _stage_pending_turn(
        self,
        *,
        campaign_id: str,
        session_id: str,
        player_id: str,
        locale: str,
        language_source: str,
        transcript_text: str,
        finalized: bool,
    ) -> None:
        self._pending_campaign_id = str(campaign_id or "").strip()
        self._pending_session_id = str(session_id or "").strip()
        self._pending_player_id = str(player_id or "").strip()
        self._pending_locale = str(locale or "").strip()
        self._pending_language_source = str(language_source or "").strip()
        self._pending_transcript_text = self._merge_transcript_text(self._pending_transcript_text, transcript_text)
        self._pending_seen_finalized = bool(self._pending_seen_finalized or finalized)

    def _clear_pending_turn(self) -> None:
        self._pending_campaign_id = ""
        self._pending_session_id = ""
        self._pending_player_id = ""
        self._pending_locale = ""
        self._pending_language_source = ""
        self._pending_seen_finalized = False
        self._pending_transcript_text = ""

    async def _flush_pending_turn(self, *, direction, reason: str) -> bool:
        transcript_text = str(self._pending_transcript_text or "").strip()
        if not transcript_text:
            return False

        player_id = str(self._pending_player_id or self.player_id or "player1").strip() or "player1"
        # Deepgram can emit multiple equivalent final segments around VAD boundaries.
        if (
            player_id == self._last_flushed_player_id
            and transcript_text == self._last_flushed_transcript_text
        ):
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    {
                        "type": "debug",
                        "event": "turn_deduped",
                        "reason": reason,
                        "player_id": player_id,
                    }
                ),
                direction,
            )
            self._clear_pending_turn()
            return False

        campaign_id = str(self._pending_campaign_id or self.campaign_id or "demo").strip() or "demo"
        session_id = str(self._pending_session_id or self.session_id or "voice").strip() or "voice"
        locale = str(self._pending_locale or self.locale or "en-US").strip() or "en-US"

        self._turn_counter += 1
        ctx = TurnContext(
            campaign_id=campaign_id,
            session_id=session_id,
            turn_id=str(self._turn_counter),
            player_id=player_id,
            transcript_text=transcript_text,
            locale=locale,
        )
        if not self._pending_seen_finalized:
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    {
                        "type": "transcript",
                        "text": transcript_text,
                        "finalized": True,
                        "timestamp": f"{time.time():.3f}",
                        "user_id": player_id,
                        "language": locale,
                        "language_source": self._pending_language_source or "buffered_flush",
                    }
                ),
                direction,
            )
        await self.push_frame(
            OutputTransportMessageUrgentFrame(
                {
                    "type": "debug",
                    "event": "turn_start",
                    "reason": reason,
                    "turn_id": ctx.turn_id,
                    "player_id": player_id,
                    "locale": locale,
                    "chars": len(transcript_text),
                }
            ),
            direction,
        )
        print(
            "voice_turn: start "
            f"turn_id={ctx.turn_id} reason={reason} player={player_id} locale={locale} chars={len(transcript_text)}"
        )
        try:
            plan = await self.controller.handle_turn(ctx)
        except Exception as e:  # pragma: no cover
            msg = f"GM controller error: {e}"
            await self.push_frame(
                OutputTransportMessageUrgentFrame({"type": "error", "error": msg}),
                direction,
            )
            await self.push_frame(TextFrame("Sorry, something went wrong on my side."), direction)
            print(f"voice_turn: error turn_id={ctx.turn_id} err={e}")
            self._clear_pending_turn()
            return False

        self._last_flushed_player_id = player_id
        self._last_flushed_transcript_text = transcript_text
        self._clear_pending_turn()

        await self.push_frame(
            OutputTransportMessageUrgentFrame({"type": "text", "text": plan.immediate_text}),
            direction,
        )
        if isinstance(plan.debug, dict) and plan.debug:
            dbg = dict(plan.debug)
            dbg.setdefault("turn_flush_reason", reason)
            await self.push_frame(
                OutputTransportMessageUrgentFrame({"type": "turn_debug", "debug": dbg}),
                direction,
            )
        tts_payloads: list[str] = [str(plan.immediate_text or "")]
        for t in plan.followups:
            await self.push_frame(
                OutputTransportMessageUrgentFrame({"type": "text", "text": t}), direction
            )
            tts_payloads.append(str(t or ""))

        # Explicitly bracket each GM turn for TTS services (e.g. ElevenLabs multi-stream)
        # that require end-of-response flushing to emit complete audio.
        if any(str(x or "").strip() for x in tts_payloads):
            await self.push_frame(LLMFullResponseStartFrame(), direction)
            for chunk_text in tts_payloads:
                await self._push_tts_text(chunk_text, direction=direction)
            await self.push_frame(LLMFullResponseEndFrame(), direction)
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    {
                        "type": "debug",
                        "event": "tts_turn_flush",
                        "turn_id": ctx.turn_id,
                    }
                ),
                direction,
            )
        await self.push_frame(
            OutputTransportMessageUrgentFrame(
                {
                    "type": "debug",
                    "event": "turn_done",
                    "reason": reason,
                    "turn_id": ctx.turn_id,
                    "gm_chars": len(str(plan.immediate_text or "")),
                }
            ),
            direction,
        )
        print(f"voice_turn: done turn_id={ctx.turn_id} reason={reason} gm_chars={len(str(plan.immediate_text or ''))}")
        return True

    async def process_frame(self, frame, direction):  # Frame, FrameDirection
        await super().process_frame(frame, direction)

        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, ErrorFrame):
            # Forward errors to the websocket client/UI immediately.
            raw_error = str(getattr(frame, "error", "") or "")
            friendly = self._friendly_error(raw_error)
            await self.push_frame(
                OutputTransportMessageUrgentFrame({"type": "error", "error": friendly}), direction
            )
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    {
                        "type": "debug",
                        "event": "error_frame",
                        "raw_error": raw_error,
                        "friendly_error": friendly,
                    }
                ),
                direction,
            )
            return

        # Buffer interim text so VAD-stop can still produce a turn when STT finalization is delayed.
        if isinstance(frame, InterimTranscriptionFrame):
            txt = str(getattr(frame, "text", "") or "").strip()
            if txt:
                async with self._lock:
                    frame_user_id = str(getattr(frame, "user_id", "") or "").strip()
                    voiceprint = self._vad_voiceprint_queue[0] if self._vad_voiceprint_queue else None
                    if self.settings_store is not None:
                        s = self.settings_store.get()
                        campaign_id = s.voice.campaign_id
                        session_id = s.voice.session_id
                        player_id, _, _ = self._resolve_player_id_from_speaker(
                            raw_user_id=frame_user_id,
                            settings=s,
                            voiceprint=voiceprint,
                        )
                        frame_lang = self._normalize_lang_tag(self._frame_language_tag(frame))
                        text_lang = self._normalize_lang_tag(self._detect_text_language_tag(txt))
                        lang_mode = str(getattr(s.prompts, "response_language_mode", "") or "").strip().lower()
                        if lang_mode == "locale":
                            locale = s.voice.locale
                            language_source = "settings.locale"
                        else:
                            locale = frame_lang or text_lang or s.voice.locale
                            language_source = "stt" if frame_lang else ("text_heuristic" if text_lang else "settings.locale")
                            if self._lang_base(frame_lang) == "en" and self._lang_base(text_lang) not in {"", "en"}:
                                locale = text_lang
                                language_source = "text_heuristic_override"
                    else:  # pragma: no cover
                        campaign_id = self.campaign_id
                        session_id = self.session_id
                        player_id = frame_user_id or self.player_id
                        frame_lang = self._normalize_lang_tag(self._frame_language_tag(frame))
                        text_lang = self._normalize_lang_tag(self._detect_text_language_tag(txt))
                        locale = frame_lang or text_lang or self.locale
                        language_source = "stt" if frame_lang else ("text_heuristic" if text_lang else "default")

                    self._stage_pending_turn(
                        campaign_id=campaign_id,
                        session_id=session_id,
                        player_id=player_id,
                        locale=locale,
                        language_source=language_source,
                        transcript_text=txt,
                        finalized=False,
                    )
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._vad_active = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self._vad_active = False
            vp = self._normalize_voiceprint(getattr(frame, "voiceprint", None))
            if vp is not None:
                self._vad_voiceprint_queue.append(vp)
                if len(self._vad_voiceprint_queue) > 8:
                    self._vad_voiceprint_queue = self._vad_voiceprint_queue[-8:]
            async with self._lock:
                flushed = await self._flush_pending_turn(direction=direction, reason="vad_stop")
                if not flushed:
                    await self.push_frame(
                        OutputTransportMessageUrgentFrame(
                            {
                                "type": "debug",
                                "event": "turn_idle_on_vad_stop",
                            }
                        ),
                        direction,
                    )
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            async with self._lock:
                frame_user_id = str(getattr(frame, "user_id", "") or "").strip()
                voiceprint = self._vad_voiceprint_queue[0] if self._vad_voiceprint_queue else None
                if self.settings_store is not None:
                    s = self.settings_store.get()
                    campaign_id = s.voice.campaign_id
                    session_id = s.voice.session_id
                    player_id, speaker_mapped, speaker_map_key = self._resolve_player_id_from_speaker(
                        raw_user_id=frame_user_id,
                        settings=s,
                        voiceprint=voiceprint,
                    )
                    frame_lang = self._normalize_lang_tag(self._frame_language_tag(frame))
                    text_lang = self._normalize_lang_tag(self._detect_text_language_tag(frame.text))
                    lang_mode = str(getattr(s.prompts, "response_language_mode", "") or "").strip().lower()
                    if lang_mode == "locale":
                        locale = s.voice.locale
                        language_source = "settings.locale"
                    else:
                        # Prefer STT-provided language, but fall back to transcript heuristics.
                        locale = frame_lang or text_lang or s.voice.locale
                        language_source = "stt" if frame_lang else ("text_heuristic" if text_lang else "settings.locale")
                        # If STT said English but transcript strongly looks non-English,
                        # trust the transcript signal for response-language routing.
                        if self._lang_base(frame_lang) == "en" and self._lang_base(text_lang) not in {"", "en"}:
                            locale = text_lang
                            language_source = "text_heuristic_override"
                else:  # pragma: no cover
                    campaign_id = self.campaign_id
                    session_id = self.session_id
                    player_id = frame_user_id or self.player_id
                    frame_lang = self._normalize_lang_tag(self._frame_language_tag(frame))
                    text_lang = self._normalize_lang_tag(self._detect_text_language_tag(frame.text))
                    locale = frame_lang or text_lang or self.locale
                    language_source = "stt" if frame_lang else ("text_heuristic" if text_lang else "default")
                    speaker_mapped = False
                    speaker_map_key = None

                if bool(getattr(frame, "finalized", False)):
                    self._update_player_voiceprint(player_id=player_id, voiceprint=voiceprint)
                    if self._vad_voiceprint_queue:
                        self._vad_voiceprint_queue.pop(0)

                # Forward resolved transcript identity to the client/UI.
                await self.push_frame(
                    OutputTransportMessageUrgentFrame(
                        {
                            "type": "transcript",
                            "text": frame.text,
                            "finalized": bool(getattr(frame, "finalized", False)),
                            "timestamp": frame.timestamp,
                            "user_id": player_id,
                            "speaker_raw_id": frame_user_id,
                            "language": locale,
                            "language_source": language_source,
                        }
                    ),
                    direction,
                )
                if speaker_mapped:
                    if self.settings_store is not None and speaker_map_key:
                        try:
                            await asyncio.to_thread(
                                self.settings_store.update,
                                {"voice": {"speaker_mappings": {speaker_map_key: player_id}}},
                            )
                        except Exception:
                            pass
                    await self.push_frame(
                        OutputTransportMessageUrgentFrame(
                            {
                                "type": "speaker_map",
                                "speaker_raw_id": frame_user_id,
                                "player_id": player_id,
                            }
                        ),
                        direction,
                    )
                await self.push_frame(frame, direction)
                self._stage_pending_turn(
                    campaign_id=campaign_id,
                    session_id=session_id,
                    player_id=player_id,
                    locale=locale,
                    language_source=language_source,
                    transcript_text=frame.text,
                    finalized=bool(getattr(frame, "finalized", False)),
                )
                await self.push_frame(
                    OutputTransportMessageUrgentFrame(
                        {
                            "type": "debug",
                            "event": "transcript_buffered",
                            "speaker_raw_id": frame_user_id,
                            "player_id": player_id,
                            "finalized": bool(getattr(frame, "finalized", False)),
                            "voiceprint_pitch_hz": (voiceprint.get("pitch_hz") if isinstance(voiceprint, dict) else None),
                            "voiceprint_zcr": (voiceprint.get("zcr") if isinstance(voiceprint, dict) else None),
                            "chars": len(self._pending_transcript_text),
                            "language": locale,
                            "language_source": language_source,
                        }
                    ),
                    direction,
                )
                # For manual transcripts (no VAD), flush finalized frames immediately.
                if bool(getattr(frame, "finalized", False)) and not self._vad_active:
                    await self._flush_pending_turn(direction=direction, reason="stt_finalized")
            return

        await self.push_frame(frame, direction)
