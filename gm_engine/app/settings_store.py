from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gm_engine.app.settings import AppSettings, PlayerVoiceProfile


_OPENAI_TTS_VOICE_PRESETS = {
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
}


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _deep_merge(a: dict, b: dict) -> dict:
    """Merge b into a recursively (dicts only)."""
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _normalize_settings(settings: AppSettings) -> AppSettings:
    def _dedupe_nonempty(items: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for x in items:
            s = str(x or "").strip()
            if not s or s in seen:
                continue
            out.append(s)
            seen.add(s)
        return out

    def _mru(items: list[str], current: str, *, limit: int = 10) -> list[str]:
        cur = str(current or "").strip()
        xs = _dedupe_nonempty(items)
        if cur:
            xs = [cur] + [x for x in xs if x != cur]
        return xs[: max(1, int(limit))]

    def _slug_id(s: str, fallback: str = "rulebook") -> str:
        x = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(s or "").strip().lower()).strip("_")
        return (x[:64] or fallback)

    def _display_name(s: str, fallback: str) -> str:
        out = str(s or "").strip()
        if out:
            return out[:64]
        fb = str(fallback or "").strip()
        return (fb[:64] or "Player")

    # Common footgun: UI bound to 0.0.0.0 but WS bound to localhost.
    # If the user opens the UI via LAN IP, the browser will attempt WS to that LAN IP
    # and fail. Align WS host to UI host in this configuration.
    if settings.voice.http_host in ("0.0.0.0", "::") and settings.voice.ws_host in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        settings.voice.ws_host = settings.voice.http_host

    # Keep recent campaign/player MRU lists in sync with the active selection.
    settings.voice.recent_campaigns = _mru(settings.voice.recent_campaigns, settings.voice.campaign_id, limit=10)

    # Clamp websocket session timeout to a sane range.
    settings.voice.ws_session_timeout_secs = max(60, min(24 * 3600, int(settings.voice.ws_session_timeout_secs)))

    # Normalize player/speaker profiles (max 8, unique player_id).
    normalized_profiles = []
    seen_players: set[str] = set()
    raw_profiles = list(settings.voice.player_profiles or [])
    for i, p in enumerate(raw_profiles):
        pid = _slug_id(
            getattr(p, "player_id", "") or (p.get("player_id") if isinstance(p, dict) else ""),
            fallback="player",
        )
        if not pid or pid in seen_players:
            continue
        seen_players.add(pid)
        display = _display_name(
            getattr(p, "display_name", "") if not isinstance(p, dict) else p.get("display_name", ""),
            fallback=f"Player {i + 1}",
        )
        vprof = str(getattr(p, "voice_profile", "") if not isinstance(p, dict) else p.get("voice_profile", "")).strip()
        normalized_profiles.append(
            PlayerVoiceProfile(player_id=pid, display_name=display, voice_profile=vprof[:120])
        )
        if len(normalized_profiles) >= 8:
            break
    if not normalized_profiles:
        fallback_pid = _slug_id(settings.voice.player_id or settings.voice.active_player_id or "player1", fallback="player1")
        normalized_profiles = [PlayerVoiceProfile(player_id=fallback_pid, display_name="Player 1", voice_profile="")]

    settings.voice.player_profiles = normalized_profiles
    profile_ids = [str(x.player_id) for x in settings.voice.player_profiles]

    # Normalize persisted speaker mappings.
    raw_map = settings.voice.speaker_mappings or {}
    norm_map: dict[str, str] = {}
    if isinstance(raw_map, dict):
        for k, v in raw_map.items():
            kk = str(k or "").strip()
            vv = _slug_id(v, fallback="")
            if not kk or not vv:
                continue
            norm_map[kk[:160]] = vv[:64]
            if len(norm_map) >= 512:
                break
    settings.voice.speaker_mappings = norm_map

    # Active player must exist in profiles. Keep legacy player_id aligned.
    active_pid = _slug_id(settings.voice.active_player_id or settings.voice.player_id, fallback="player1")
    if active_pid not in profile_ids:
        active_pid = profile_ids[0]
    settings.voice.active_player_id = active_pid
    settings.voice.player_id = active_pid
    settings.voice.recent_players = _mru(settings.voice.recent_players, active_pid, limit=10)

    # Keep GUI model dropdown options in sync with selected models.
    stt_provider = str(settings.openai.stt_provider or "").strip().lower()
    if stt_provider not in {"openai", "deepgram"}:
        stt_provider = "openai"
    settings.openai.stt_provider = stt_provider
    llm_provider = str(settings.openai.llm_provider or "").strip().lower()
    if llm_provider not in {"openai", "codex_chatgpt"}:
        llm_provider = "openai"
    settings.openai.llm_provider = llm_provider
    dg_profile = str(getattr(settings.openai, "deepgram_feature_profile", "") or "").strip().lower()
    if dg_profile not in {
        "speaker_diarization",
        "multilingual",
        "auto_language_detection",
        "multilingual_diarization",
    }:
        dg_profile = "speaker_diarization"
    settings.openai.deepgram_feature_profile = dg_profile
    tts_provider = str(settings.openai.tts_provider or "").strip().lower()
    if tts_provider not in {"openai", "elevenlabs"}:
        tts_provider = "openai"
    settings.openai.tts_provider = tts_provider

    # Provider-specific normalization for TTS model/voice.
    if settings.openai.tts_provider == "openai":
        tts_model = str(settings.openai.tts_model or "").strip()
        if not tts_model.lower().startswith("gpt-"):
            settings.openai.tts_model = "gpt-4o-mini-tts"
        tts_voice = str(settings.openai.tts_voice or "").strip().lower()
        settings.openai.tts_voice = tts_voice if tts_voice in _OPENAI_TTS_VOICE_PRESETS else "alloy"

    # Pipecat ElevenLabs websocket TTS is most stable with v2.5 models.
    # Auto-migrate deprecated/unstable model ids to avoid "text-only, no audio" turns.
    if settings.openai.tts_provider == "elevenlabs":
        tts_model = str(settings.openai.tts_model or "").strip().lower()
        if tts_model in {"", "eleven_multilingual_v2", "eleven_turbo_v2", "eleven_flash_v2"}:
            settings.openai.tts_model = "eleven_turbo_v2_5"

    settings.openai.stt_provider_options = _mru(
        settings.openai.stt_provider_options, settings.openai.stt_provider, limit=8
    )
    settings.openai.llm_provider_options = _mru(
        settings.openai.llm_provider_options, settings.openai.llm_provider, limit=8
    )
    settings.openai.deepgram_feature_profile_options = _mru(
        settings.openai.deepgram_feature_profile_options,
        settings.openai.deepgram_feature_profile,
        limit=8,
    )
    settings.openai.tts_provider_options = _mru(
        settings.openai.tts_provider_options, settings.openai.tts_provider, limit=8
    )
    settings.openai.llm_model_options = _mru(settings.openai.llm_model_options, settings.openai.llm_model, limit=20)
    settings.openai.stt_model_options = _mru(settings.openai.stt_model_options, settings.openai.stt_model, limit=20)
    settings.openai.tts_model_options = _mru(settings.openai.tts_model_options, settings.openai.tts_model, limit=20)
    deprecated_el_models = {"eleven_multilingual_v2", "eleven_turbo_v2", "eleven_flash_v2"}
    settings.openai.tts_model_options = [
        x for x in settings.openai.tts_model_options if str(x or "").strip().lower() not in deprecated_el_models
    ]
    for required in ["gpt-4o-mini-tts", "eleven_turbo_v2_5", "eleven_flash_v2_5"]:
        if required not in settings.openai.tts_model_options:
            settings.openai.tts_model_options.append(required)
    settings.openai.embedding_model_options = _mru(
        settings.openai.embedding_model_options, settings.openai.embedding_model, limit=20
    )

    # Keep split and single collection names aligned for backwards compatibility.
    if not settings.knowledge.collection.strip():
        settings.knowledge.collection = "gm_knowledge"
    if not settings.knowledge.game_collection.strip():
        settings.knowledge.game_collection = settings.knowledge.collection or "gm_knowledge_game"
    if not settings.knowledge.guidance_collection.strip():
        settings.knowledge.guidance_collection = "gm_knowledge_guidance"
    if settings.knowledge.split_collections:
        # In split mode, collection remains as legacy fallback only.
        pass
    else:
        # In single mode, use the legacy collection and keep game_collection aligned.
        settings.knowledge.game_collection = settings.knowledge.collection

    # Normalize primary rulebook settings (server-local PDF path).
    src = str(settings.knowledge.primary_rulebook_source or "").strip().lower()
    if src not in {"path", "doc"}:
        src = "path"
    settings.knowledge.primary_rulebook_source = src
    # Keep the exact selected doc id from the UI. It must match knowledge index entries
    # verbatim (historical uploads may exceed legacy slug lengths).
    settings.knowledge.primary_rulebook_doc_choice = str(
        settings.knowledge.primary_rulebook_doc_choice or ""
    ).strip()
    settings.knowledge.primary_rulebook_path = str(settings.knowledge.primary_rulebook_path or "").strip()
    rb_doc_id = str(settings.knowledge.primary_rulebook_doc_id or "").strip()
    if not rb_doc_id and settings.knowledge.primary_rulebook_path:
        rb_doc_id = Path(settings.knowledge.primary_rulebook_path).stem
    settings.knowledge.primary_rulebook_doc_id = _slug_id(rb_doc_id, fallback="rulebook")
    settings.knowledge.primary_rulebook_ruleset = str(settings.knowledge.primary_rulebook_ruleset or "").strip().lower()
    rb_kind = str(settings.knowledge.primary_rulebook_doc_kind or "").strip().lower()
    if rb_kind not in {"rulebook", "adventure", "lorebook", "gm_advice", "other"}:
        rb_kind = "rulebook"
    settings.knowledge.primary_rulebook_doc_kind = rb_kind
    rb_target = str(settings.knowledge.primary_rulebook_collection_target or "").strip().lower()
    if settings.knowledge.split_collections:
        if rb_target not in {"game", "guidance"}:
            rb_target = "game"
    else:
        rb_target = "default"
    settings.knowledge.primary_rulebook_collection_target = rb_target
    if settings.knowledge.primary_rulebook_source == "doc" and not settings.knowledge.primary_rulebook_doc_choice:
        settings.knowledge.primary_rulebook_doc_choice = settings.knowledge.primary_rulebook_doc_id

    # Prompt language policy normalization.
    lang_mode = str(settings.prompts.response_language_mode or "").strip().lower()
    if lang_mode not in {"player", "locale"}:
        lang_mode = "player"
    settings.prompts.response_language_mode = lang_mode

    return settings


@dataclass
class SettingsStore:
    path: Path

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._settings = self._load_or_init()

    def _load_or_init(self) -> AppSettings:
        # Start from env defaults so existing .env workflows keep working.
        defaults = AppSettings()
        defaults.voice.campaign_id = os.environ.get("GM_CAMPAIGN_ID", defaults.voice.campaign_id)
        defaults.voice.session_id = os.environ.get("GM_SESSION_ID", defaults.voice.session_id)
        defaults.voice.player_id = os.environ.get("GM_PLAYER_ID", defaults.voice.player_id)
        defaults.voice.active_player_id = os.environ.get("GM_ACTIVE_PLAYER_ID", defaults.voice.player_id)
        defaults.voice.locale = os.environ.get("GM_LOCALE", defaults.voice.locale)
        defaults.voice.ws_host = os.environ.get("GM_WS_HOST", defaults.voice.ws_host)
        defaults.voice.ws_port = int(os.environ.get("GM_WS_PORT", str(defaults.voice.ws_port)))
        defaults.voice.ws_session_timeout_secs = int(
            os.environ.get("GM_WS_SESSION_TIMEOUT_SECS", str(defaults.voice.ws_session_timeout_secs))
        )
        defaults.voice.http_host = os.environ.get("GM_HTTP_HOST", defaults.voice.http_host)
        defaults.voice.http_port = int(os.environ.get("GM_HTTP_PORT", str(defaults.voice.http_port)))

        defaults.openai.llm_model = os.environ.get("GM_LLM_MODEL", defaults.openai.llm_model)
        defaults.openai.llm_provider = os.environ.get("GM_LLM_PROVIDER", defaults.openai.llm_provider)
        defaults.openai.stt_provider = os.environ.get("GM_STT_PROVIDER", defaults.openai.stt_provider)
        defaults.openai.tts_provider = os.environ.get("GM_TTS_PROVIDER", defaults.openai.tts_provider)
        defaults.openai.stt_model = os.environ.get("GM_STT_MODEL", defaults.openai.stt_model)
        defaults.openai.tts_model = os.environ.get("GM_TTS_MODEL", defaults.openai.tts_model)
        defaults.openai.tts_voice = os.environ.get("GM_TTS_VOICE", defaults.openai.tts_voice)
        defaults.openai.embedding_model = os.environ.get("GM_EMBEDDING_MODEL", defaults.openai.embedding_model)

        defaults.knowledge.qdrant_url = os.environ.get("QDRANT_URL", defaults.knowledge.qdrant_url)
        defaults.knowledge.collection = os.environ.get("GM_QDRANT_COLLECTION", defaults.knowledge.collection)
        defaults.knowledge.split_collections = str(
            os.environ.get("GM_KB_SPLIT_COLLECTIONS", str(defaults.knowledge.split_collections))
        ).strip().lower() in {"1", "true", "yes", "on"}
        defaults.knowledge.game_collection = os.environ.get(
            "GM_QDRANT_GAME_COLLECTION", defaults.knowledge.game_collection
        )
        defaults.knowledge.guidance_collection = os.environ.get(
            "GM_QDRANT_GUIDANCE_COLLECTION", defaults.knowledge.guidance_collection
        )
        defaults.knowledge.backend = os.environ.get("GM_KB_BACKEND", defaults.knowledge.backend)
        defaults.knowledge.local_path = os.environ.get("GM_QDRANT_LOCAL_PATH", defaults.knowledge.local_path)
        defaults.knowledge.primary_rulebook_path = os.environ.get(
            "GM_PRIMARY_RULEBOOK_PATH", defaults.knowledge.primary_rulebook_path
        )
        defaults.knowledge.primary_rulebook_source = os.environ.get(
            "GM_PRIMARY_RULEBOOK_SOURCE", defaults.knowledge.primary_rulebook_source
        )
        defaults.knowledge.primary_rulebook_doc_choice = os.environ.get(
            "GM_PRIMARY_RULEBOOK_DOC_CHOICE", defaults.knowledge.primary_rulebook_doc_choice
        )
        defaults.knowledge.primary_rulebook_doc_id = os.environ.get(
            "GM_PRIMARY_RULEBOOK_DOC_ID", defaults.knowledge.primary_rulebook_doc_id
        )
        defaults.knowledge.primary_rulebook_ruleset = os.environ.get(
            "GM_PRIMARY_RULEBOOK_RULESET", defaults.knowledge.primary_rulebook_ruleset
        )
        defaults.knowledge.primary_rulebook_doc_kind = os.environ.get(
            "GM_PRIMARY_RULEBOOK_DOC_KIND", defaults.knowledge.primary_rulebook_doc_kind
        )
        defaults.knowledge.primary_rulebook_collection_target = os.environ.get(
            "GM_PRIMARY_RULEBOOK_COLLECTION_TARGET", defaults.knowledge.primary_rulebook_collection_target
        )
        defaults.knowledge.primary_rulebook_auto_ingest = str(
            os.environ.get("GM_PRIMARY_RULEBOOK_AUTO_INGEST", str(defaults.knowledge.primary_rulebook_auto_ingest))
        ).strip().lower() in {"1", "true", "yes", "on"}
        defaults.knowledge.primary_rulebook_auto_activate = str(
            os.environ.get("GM_PRIMARY_RULEBOOK_AUTO_ACTIVATE", str(defaults.knowledge.primary_rulebook_auto_activate))
        ).strip().lower() in {"1", "true", "yes", "on"}
        defaults.prompts.response_language_mode = os.environ.get(
            "GM_RESPONSE_LANGUAGE_MODE", defaults.prompts.response_language_mode
        )

        if not self.path.exists():
            defaults = _normalize_settings(defaults)
            _atomic_write_json(self.path, defaults.model_dump())
            return defaults

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            defaults = _normalize_settings(defaults)
            _atomic_write_json(self.path, defaults.model_dump())
            return defaults

        # Merge to allow forward-compatible additions.
        merged = _deep_merge(defaults.model_dump(), raw if isinstance(raw, dict) else {})
        settings = _normalize_settings(AppSettings.model_validate(merged))
        # Normalize: always write back once so file is self-healing.
        _atomic_write_json(self.path, settings.model_dump())
        return settings

    def get(self) -> AppSettings:
        with self._lock:
            return AppSettings.model_validate(self._settings.model_dump())

    def update(self, patch: dict[str, Any]) -> AppSettings:
        with self._lock:
            merged = _deep_merge(self._settings.model_dump(), patch)
            settings = _normalize_settings(AppSettings.model_validate(merged))
            self._settings = settings
            _atomic_write_json(self.path, settings.model_dump())
            # Return a detached copy while still under lock; calling self.get()
            # here would attempt to re-acquire the same non-reentrant lock.
            return AppSettings.model_validate(settings.model_dump())
