export type PlayerProfile = {
  player_id: string;
  display_name: string;
  voice_profile: string;
};

export type VoiceSettings = {
  ws_host: string;
  ws_port: number;
  ws_session_timeout_secs: number;
  http_host: string;
  http_port: number;
  campaign_id: string;
  session_id: string;
  player_id: string;
  active_player_id: string;
  auto_select_active_speaker: boolean;
  player_profiles: PlayerProfile[];
  speaker_mappings: Record<string, string>;
  locale: string;
  recent_campaigns: string[];
  recent_players: string[];
};

export type OpenAISettings = {
  stt_provider: string;
  tts_provider: string;
  llm_provider: string;
  deepgram_feature_profile: string;
  llm_model: string;
  stt_model: string;
  tts_model: string;
  tts_voice: string;
  embedding_model: string;
  llm_model_options: string[];
  llm_provider_options: string[];
  stt_model_options: string[];
  deepgram_feature_profile_options: string[];
  stt_provider_options: string[];
  tts_provider_options: string[];
  tts_model_options: string[];
  embedding_model_options: string[];
};

export type KnowledgeSettings = {
  enabled: boolean;
  backend: string;
  qdrant_url: string;
  local_path: string;
  collection: string;
  split_collections: boolean;
  game_collection: string;
  guidance_collection: string;
  top_k: number;
  active_doc_ids: string[];
  primary_rulebook_source: "path" | "doc" | string;
  primary_rulebook_doc_choice: string;
  primary_rulebook_path: string;
  primary_rulebook_doc_id: string;
  primary_rulebook_ruleset: string;
  primary_rulebook_doc_kind: string;
  primary_rulebook_collection_target: string;
  primary_rulebook_auto_ingest: boolean;
  primary_rulebook_auto_activate: boolean;
  chunk_max_chars: number;
  chunk_overlap: number;
};

export type PromptSettings = {
  intent_classify_system: string;
  resolve_system: string;
  resolve_user_template: string;
  include_memory: boolean;
  memory_turns: number;
  response_language_mode: "player" | "locale" | string;
};

export type AppSettings = {
  version: number;
  voice: VoiceSettings;
  openai: OpenAISettings;
  knowledge: KnowledgeSettings;
  prompts: PromptSettings;
};

export type ServerStatus = {
  type: "server_status";
  openai_api_key_present: boolean;
  deepgram_api_key_present: boolean;
  elevenlabs_api_key_present: boolean;
  codex_chatgpt_available?: boolean;
  codex_chatgpt_status?: string;
  openai_base_url: string;
  settings: AppSettings;
};

export type SecretRecord = {
  present: boolean;
  masked: string;
  secret: boolean;
};

export type SecretsMap = Record<string, SecretRecord>;

export type SecretsResponse = {
  type: "secrets";
  secrets: SecretsMap;
  restart_required?: boolean;
  restart_keys?: string[];
};

export type KBDocument = {
  doc_id: string;
  filename?: string;
  ruleset?: string;
  doc_kind?: string;
  collection_target?: string;
  path?: string;
  status?: string;
  created_at?: number;
  chunks?: number;
  error?: string;
  source?: string;
};

export type SetupSystemHint = {
  id: string;
  name: string;
  aliases?: string;
  setup_system?: string;
  ruleset?: string;
  beginner_hint?: string;
  summary?: string;
  source?: string;
  url?: string;
  score?: number;
};

export type ElevenLabsVoice = {
  voice_id: string;
  name: string;
  category?: string;
  labels?: Record<string, string>;
};

export type MemoryEntry = {
  ts?: string;
  player_text?: string;
  gm_text?: string;
  player_id?: string;
  session_id?: string;
  [k: string]: unknown;
};

export type SearchResult = {
  text?: string;
  score?: number;
  meta?: Record<string, unknown>;
  [k: string]: unknown;
};
