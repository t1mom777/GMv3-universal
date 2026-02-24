from __future__ import annotations

from pydantic import BaseModel, Field


class PlayerVoiceProfile(BaseModel):
    player_id: str = "player1"
    display_name: str = "Player 1"
    voice_profile: str = ""


class VoiceSettings(BaseModel):
    ws_host: str = "0.0.0.0"
    ws_port: int = 8765
    # Session timeout for stale websocket clients (seconds).
    ws_session_timeout_secs: int = 900
    http_host: str = "0.0.0.0"
    http_port: int = 8000

    campaign_id: str = "demo"
    session_id: str = "ws"
    player_id: str = "player1"
    # Active speaker for the next turn (used by live voice/transcript routing).
    active_player_id: str = "player1"
    # If true, finalized diarized transcript speaker will auto-select active player in UI/runtime.
    auto_select_active_speaker: bool = True
    # Up to 8 speaker/player profiles for table play.
    player_profiles: list[PlayerVoiceProfile] = Field(
        default_factory=lambda: [PlayerVoiceProfile(player_id="player1", display_name="Player 1")]
    )
    # Persisted speaker-id -> player-id assignments.
    # Key format: "{campaign_id}|{session_id}|{speaker_raw_id}".
    speaker_mappings: dict[str, str] = Field(default_factory=dict)
    locale: str = "en-US"
    # Convenience: keep a small MRU list so the UI can quickly switch between
    # campaigns/players without retyping. (Hard cap: 10.)
    recent_campaigns: list[str] = Field(default_factory=list)
    recent_players: list[str] = Field(default_factory=list)


class OpenAIModelSettings(BaseModel):
    stt_provider: str = "openai"  # "openai" | "deepgram"
    tts_provider: str = "openai"  # "openai" | "elevenlabs"
    # LLM provider:
    # - openai: OpenAI API key mode
    # - codex_chatgpt: codex login session mode (ChatGPT account)
    llm_provider: str = "openai"
    # Deepgram STT preset profiles:
    # - speaker_diarization: Nova-3 + diarize=true
    # - multilingual: Nova-3 + language=multi
    # - auto_language_detection: Nova-3 + detect_language=true
    # - multilingual_diarization: Nova-3 + language=multi + diarize=true
    deepgram_feature_profile: str = "speaker_diarization"
    llm_model: str = "gpt-4o-mini"
    stt_model: str = "gpt-4o-transcribe"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    embedding_model: str = "text-embedding-3-small"
    # User-editable model presets shown in GUI dropdowns.
    llm_model_options: list[str] = Field(
        default_factory=lambda: ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o", "o3-mini"]
    )
    llm_provider_options: list[str] = Field(default_factory=lambda: ["openai", "codex_chatgpt"])
    stt_model_options: list[str] = Field(
        default_factory=lambda: ["gpt-4o-transcribe", "whisper-1", "nova-3-general", "nova-2"]
    )
    deepgram_feature_profile_options: list[str] = Field(
        default_factory=lambda: [
            "speaker_diarization",
            "multilingual",
            "auto_language_detection",
            "multilingual_diarization",
        ]
    )
    stt_provider_options: list[str] = Field(default_factory=lambda: ["openai", "deepgram"])
    tts_provider_options: list[str] = Field(default_factory=lambda: ["openai", "elevenlabs"])
    tts_model_options: list[str] = Field(
        default_factory=lambda: ["gpt-4o-mini-tts", "eleven_turbo_v2_5", "eleven_flash_v2_5"]
    )
    embedding_model_options: list[str] = Field(
        default_factory=lambda: ["text-embedding-3-small", "text-embedding-3-large"]
    )


class KnowledgeSettings(BaseModel):
    enabled: bool = False

    # Qdrant backend mode:
    # - local: uses qdrant-client local mode (no Docker required)
    # - remote: connects to a running Qdrant server via URL
    backend: str = "local"  # "local" | "remote"
    qdrant_url: str = "http://localhost:6333"
    local_path: str = "data/qdrant_local"
    # Legacy single-collection mode.
    collection: str = "gm_knowledge"
    # Split collections mode:
    # - game_collection: rulebooks, lore, adventures, campaign files
    # - guidance_collection: GM best-practices / meta guidance
    split_collections: bool = True
    game_collection: str = "gm_knowledge_game"
    guidance_collection: str = "gm_knowledge_guidance"

    # Retrieval defaults.
    top_k: int = 5
    active_doc_ids: list[str] = Field(default_factory=list)

    # Primary rulebook (server-local PDF path), managed from GUI settings.
    # Relative paths are resolved from the repository root.
    primary_rulebook_source: str = "path"  # "path" | "doc"
    primary_rulebook_doc_choice: str = ""
    primary_rulebook_path: str = "Numenera.pdf"
    primary_rulebook_doc_id: str = "numenera_core"
    primary_rulebook_ruleset: str = "numenera"
    primary_rulebook_doc_kind: str = "rulebook"
    primary_rulebook_collection_target: str = "game"  # "game" | "guidance" | "default"
    primary_rulebook_auto_ingest: bool = True
    primary_rulebook_auto_activate: bool = True

    # Chunking defaults for PDF ingest.
    chunk_max_chars: int = 1200
    chunk_overlap: int = 120


class PromptSettings(BaseModel):
    intent_classify_system: str = "Classify player intent into one of: action, question, dialogue, meta."
    resolve_system: str = (
        "You are a strict, consistent tabletop GM assistant. "
        "You must be concise and deterministic. If rules are unclear, ask a clarifying question."
    )
    resolve_user_template: str = (
        "Player said: {{transcript}}\n\n"
        "Recent memory:\n{{memory}}\n\n"
        "State snapshot: {{state_json}}\n\n"
        "Relevant rules/lore:\n{{snippets}}\n\n"
        "Reply only with the GM's narration (1-3 concise sentences). Do not output JSON."
    )

    include_memory: bool = True
    memory_turns: int = 12
    # Response language policy:
    # - player: follow the language of the player's latest utterance
    # - locale: force configured locale language
    response_language_mode: str = "player"  # "player" | "locale"


class AppSettings(BaseModel):
    version: int = 1
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    openai: OpenAIModelSettings = Field(default_factory=OpenAIModelSettings)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)
    prompts: PromptSettings = Field(default_factory=PromptSettings)
