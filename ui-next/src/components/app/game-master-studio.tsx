"use client";

import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  Brain,
  Bug,
  CheckCircle2,
  CloudUpload,
  Globe,
  KeyRound,
  LoaderCircle,
  MessageSquare,
  Mic2,
  RefreshCw,
  Save,
  Search,
  Server,
  Sparkles,
  Trash2,
  Waves,
} from "lucide-react";
import { toast } from "sonner";

import {
  clearMemory,
  deleteDoc,
  getElevenLabsVoices,
  getKbDocuments,
  getMemory,
  getSecrets,
  getServerStatus,
  getSettings,
  getWsUrl,
  ingestDoc,
  kbSearch,
  newCampaign,
  resetCampaign,
  resumeLatestCampaign,
  searchSystems,
  syncRulebook,
  updateSecrets,
  updateSettings,
  uploadPdfInChunks,
} from "@/lib/api";
import type { AppSettings, MemoryEntry, SearchResult, SetupSystemHint } from "@/lib/types";
import { cn, slugify } from "@/lib/utils";
import { useUIStore } from "@/store/ui-store";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";

const tabLabels = {
  setup: "Setup",
  play: "Play",
  advanced: "Advanced",
} as const;

type SecretAction = "keep" | "set" | "clear";

const secretFields = [
  { key: "OPENAI_API_KEY", label: "OpenAI API Key" },
  { key: "DEEPGRAM_API_KEY", label: "Deepgram API Key" },
  { key: "ELEVENLABS_API_KEY", label: "ElevenLabs API Key" },
  { key: "OPENAI_BASE_URL", label: "OpenAI Base URL" },
] as const;

const deepgramFeaturePresets = [
  {
    id: "speaker_diarization",
    label: "Speaker Diarization",
    summary: "Nova-3 + diarize=true",
  },
  {
    id: "multilingual",
    label: "Language Switching / Multilingual",
    summary: "Nova-3 + language=multi",
  },
  {
    id: "auto_language_detection",
    label: "Auto Language Detection",
    summary: "Nova-3 + detect_language=true",
  },
  {
    id: "multilingual_diarization",
    label: "Streaming Multilingual + Diarization",
    summary: "Nova-3 + language=multi + diarize=true",
  },
] as const;

function deepgramPresetLabel(id: string): string {
  const hit = deepgramFeaturePresets.find((x) => x.id === id);
  return hit ? hit.label : id;
}

function deepgramPresetSummary(id: string): string {
  const hit = deepgramFeaturePresets.find((x) => x.id === id);
  return hit ? hit.summary : "Nova-3 preset";
}

function cloneSettings(settings: AppSettings): AppSettings {
  return JSON.parse(JSON.stringify(settings)) as AppSettings;
}

function ensurePlayerProfiles(settings: AppSettings): AppSettings {
  if (typeof settings.voice.auto_select_active_speaker !== "boolean") {
    settings.voice.auto_select_active_speaker = true;
  }
  if (!settings.openai.llm_provider) {
    settings.openai.llm_provider = "openai";
  }
  if (!Array.isArray(settings.openai.llm_provider_options) || settings.openai.llm_provider_options.length === 0) {
    settings.openai.llm_provider_options = ["openai", "codex_chatgpt"];
  }
  if (!settings.openai.deepgram_feature_profile) {
    settings.openai.deepgram_feature_profile = "speaker_diarization";
  }
  if (
    !Array.isArray(settings.openai.deepgram_feature_profile_options) ||
    settings.openai.deepgram_feature_profile_options.length === 0
  ) {
    settings.openai.deepgram_feature_profile_options = deepgramFeaturePresets.map((x) => x.id);
  }
  if (!settings.voice.player_profiles || settings.voice.player_profiles.length === 0) {
    settings.voice.player_profiles = [{ player_id: "player1", display_name: "Player 1", voice_profile: "" }];
  }
  settings.voice.player_profiles = settings.voice.player_profiles.slice(0, 8);
  if (!settings.voice.player_profiles.find((p) => p.player_id === settings.voice.active_player_id)) {
    settings.voice.active_player_id = settings.voice.player_profiles[0].player_id;
  }
  settings.voice.player_id = settings.voice.active_player_id;
  return settings;
}

function makeId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

type TraceLevel = "info" | "warn" | "error";

type TraceEntry = {
  id: string;
  ts: number;
  level: TraceLevel;
  msg: string;
  data?: unknown;
};

type InterruptSensitivity = "balanced" | "high" | "max";

type Voiceprint = {
  pitch_hz: number;
  zcr: number;
};

function safeTraceData(v: unknown): string {
  if (v == null) return "";
  try {
    const s = JSON.stringify(v);
    return s.length > 400 ? `${s.slice(0, 400)}...` : s;
  } catch {
    return String(v);
  }
}

function bytesToBase64(bytes: Uint8Array): string {
  const chunk = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += chunk) {
    const part = bytes.subarray(i, i + chunk);
    binary += String.fromCharCode(...part);
  }
  return btoa(binary);
}

function base64ToBytes(base64: string): Uint8Array {
  const clean = String(base64 || "").trim();
  if (!clean) return new Uint8Array(0);
  const binary = atob(clean);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    out[i] = binary.charCodeAt(i);
  }
  return out;
}

function concatBytes(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

function downsampleToPCM16(input: Float32Array, inputSampleRate: number, outSampleRate = 16000): Uint8Array {
  if (!input.length) return new Uint8Array(0);
  const ratio = inputSampleRate / outSampleRate;
  const outLen = Math.max(1, Math.floor(input.length / Math.max(ratio, 1e-6)));
  const buffer = new ArrayBuffer(outLen * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < outLen; i += 1) {
    const srcIdx = Math.min(input.length - 1, Math.floor(i * ratio));
    const sample = Math.max(-1, Math.min(1, input[srcIdx]));
    const s16 = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    view.setInt16(i * 2, Math.round(s16), true);
  }
  return new Uint8Array(buffer);
}

function pcm16BytesToFloat32(bytes: Uint8Array): Float32Array {
  const sampleCount = Math.floor(bytes.length / 2);
  const out = new Float32Array(sampleCount);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let i = 0; i < sampleCount; i += 1) {
    const s16 = view.getInt16(i * 2, true);
    out[i] = s16 / 32768;
  }
  return out;
}

function resampleLinear(input: Float32Array, inRate: number, outRate: number): Float32Array {
  if (!input.length || inRate <= 0 || outRate <= 0 || inRate === outRate) return input;
  const ratio = inRate / outRate;
  const outLen = Math.max(1, Math.floor(input.length / ratio));
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i += 1) {
    const pos = i * ratio;
    const idx = Math.floor(pos);
    const frac = pos - idx;
    const a = input[Math.min(input.length - 1, idx)];
    const b = input[Math.min(input.length - 1, idx + 1)];
    out[i] = a + (b - a) * frac;
  }
  return out;
}

function estimatePitchHz(input: Float32Array, sampleRate: number): number {
  if (!input.length || sampleRate <= 0) return 0;
  const minHz = 70;
  const maxHz = 360;
  const minLag = Math.max(1, Math.floor(sampleRate / maxHz));
  const maxLag = Math.max(minLag + 1, Math.floor(sampleRate / minHz));
  const n = input.length;
  if (maxLag >= n - 1) return 0;

  let bestLag = 0;
  let bestCorr = 0;
  for (let lag = minLag; lag <= maxLag; lag += 1) {
    let corr = 0;
    const lim = n - lag;
    for (let i = 0; i < lim; i += 1) {
      corr += input[i] * input[i + lag];
    }
    if (corr > bestCorr) {
      bestCorr = corr;
      bestLag = lag;
    }
  }
  if (bestLag <= 0 || bestCorr <= 1e-6) return 0;
  return sampleRate / bestLag;
}

export function GameMasterStudio() {
  const queryClient = useQueryClient();

  const activeTab = useUIStore((s) => s.activeTab);
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  const wsStatus = useUIStore((s) => s.wsStatus);
  const setWsStatus = useUIStore((s) => s.setWsStatus);
  const wsUrl = useUIStore((s) => s.wsUrl);
  const setWsUrl = useUIStore((s) => s.setWsUrl);
  const messages = useUIStore((s) => s.messages);
  const addMessage = useUIStore((s) => s.addMessage);
  const clearMessages = useUIStore((s) => s.clearMessages);

  const wsRef = React.useRef<WebSocket | null>(null);
  const audioCtxRef = React.useRef<AudioContext | null>(null);
  const micStreamRef = React.useRef<MediaStream | null>(null);
  const micSourceRef = React.useRef<MediaStreamAudioSourceNode | null>(null);
  const micProcessorRef = React.useRef<ScriptProcessorNode | null>(null);
  const sendBufferRef = React.useRef<Uint8Array>(new Uint8Array(0));
  const talkRef = React.useRef(false);
  const playheadRef = React.useRef(0);
  const activeOutSourcesRef = React.useRef<Set<AudioBufferSourceNode>>(new Set());
  const vadStartCandidateAtRef = React.useRef<number | null>(null);
  const vadStopCandidateAtRef = React.useRef<number | null>(null);
  const voiceModeRef = React.useRef<"ptt" | "auto">("ptt");
  const vadThresholdRef = React.useRef(0.02);
  const interruptSensitivityRef = React.useRef<InterruptSensitivity>("high");
  const gmSpeakingRef = React.useRef(false);
  const activeUserIdRef = React.useRef("player1");
  const autoSpeakerRef = React.useRef(true);
  const traceEnabledRef = React.useRef(false);
  const sentAudioFramesRef = React.useRef(0);
  const recvAudioFramesRef = React.useRef(0);
  const lastAudioTraceAtRef = React.useRef(0);
  const ttsTurnAudioFramesRef = React.useRef(0);
  const ttsTurnAudioBytesRef = React.useRef(0);
  const utteranceSamplesRef = React.useRef(0);
  const utteranceZeroCrossRef = React.useRef(0);
  const utterancePitchSumRef = React.useRef(0);
  const utterancePitchWeightRef = React.useRef(0);
  const utteranceChunkCountRef = React.useRef(0);

  const [draft, setDraft] = React.useState<AppSettings | null>(null);
  const [dirty, setDirty] = React.useState(false);
  const [manualText, setManualText] = React.useState("");
  const [voiceMode, setVoiceMode] = React.useState<"ptt" | "auto">("ptt");
  const [vadThreshold, setVadThreshold] = React.useState(0.02);
  const [interruptSensitivity, setInterruptSensitivity] = React.useState<InterruptSensitivity>(() => {
    if (typeof window === "undefined") return "high";
    const raw = window.localStorage.getItem("vgm_interrupt_sensitivity");
    if (raw === "balanced" || raw === "high" || raw === "max") return raw;
    return "high";
  });
  const [vadLevel, setVadLevel] = React.useState(0);
  const [isTalking, setIsTalking] = React.useState(false);
  const [gmSpeaking, setGmSpeaking] = React.useState(false);
  const [micState, setMicState] = React.useState<"idle" | "ready" | "error">("idle");
  const [traceEnabled, setTraceEnabled] = React.useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("vgm_voice_trace_enabled") === "1";
  });
  const [traceEntries, setTraceEntries] = React.useState<TraceEntry[]>([]);

  const [setupQuery, setSetupQuery] = React.useState("");
  const [setupHints, setSetupHints] = React.useState<SetupSystemHint[]>([]);
  const [selectedHintId, setSelectedHintId] = React.useState("");

  const [rulebookFile, setRulebookFile] = React.useState<File | null>(null);
  const [uploadPct, setUploadPct] = React.useState(0);

  const [kbQuery, setKbQuery] = React.useState("");
  const [kbResults, setKbResults] = React.useState<SearchResult[]>([]);

  const [memoryScope, setMemoryScope] = React.useState<"campaign" | "session" | "player">("campaign");

  const [secretActions, setSecretActions] = React.useState<Record<string, SecretAction>>({
    OPENAI_API_KEY: "keep",
    DEEPGRAM_API_KEY: "keep",
    ELEVENLABS_API_KEY: "keep",
    OPENAI_BASE_URL: "keep",
  });
  const [secretValues, setSecretValues] = React.useState<Record<string, string>>({
    OPENAI_API_KEY: "",
    DEEPGRAM_API_KEY: "",
    ELEVENLABS_API_KEY: "",
    OPENAI_BASE_URL: "",
  });

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: getSettings,
  });

  const statusQuery = useQuery({
    queryKey: ["status"],
    queryFn: getServerStatus,
    refetchInterval: 10_000,
  });

  const wsUrlQuery = useQuery({
    queryKey: ["ws-url"],
    queryFn: getWsUrl,
  });

  const secretsQuery = useQuery({
    queryKey: ["secrets"],
    queryFn: getSecrets,
  });

  const docsQuery = useQuery({
    queryKey: ["kb-docs"],
    queryFn: getKbDocuments,
    refetchInterval: activeTab === "advanced" ? 7_000 : false,
  });

  const memoryQuery = useQuery({
    queryKey: [
      "memory",
      memoryScope,
      draft?.voice.session_id || "",
      draft?.voice.active_player_id || draft?.voice.player_id || "",
    ],
    queryFn: () =>
      getMemory({
        scope: memoryScope,
        limit: 80,
        session_id: draft?.voice.session_id,
        player_id: draft?.voice.active_player_id || draft?.voice.player_id,
      }),
    enabled: !!draft && activeTab === "advanced",
  });

  const elevenVoicesQuery = useQuery({
    queryKey: ["eleven-voices"],
    queryFn: getElevenLabsVoices,
    enabled:
      !!draft &&
      draft.openai.tts_provider === "elevenlabs" &&
      !!secretsQuery.data?.secrets?.ELEVENLABS_API_KEY?.present,
  });

  React.useEffect(() => {
    if (settingsQuery.data && !dirty) {
      setDraft(ensurePlayerProfiles(cloneSettings(settingsQuery.data)));
    }
  }, [settingsQuery.data, dirty]);

  React.useEffect(() => {
    if (!wsUrl && wsUrlQuery.data) {
      setWsUrl(wsUrlQuery.data);
    }
  }, [wsUrl, wsUrlQuery.data, setWsUrl]);

  React.useEffect(() => {
    traceEnabledRef.current = traceEnabled;
    if (typeof window !== "undefined") {
      window.localStorage.setItem("vgm_voice_trace_enabled", traceEnabled ? "1" : "0");
    }
  }, [traceEnabled]);

  const addTrace = React.useCallback((level: TraceLevel, msg: string, data?: unknown) => {
    if (!traceEnabledRef.current) return;
    setTraceEntries((prev) => {
      const next = [...prev, { id: makeId(), ts: Date.now(), level, msg, data }];
      return next.slice(-500);
    });
  }, []);

  React.useEffect(() => {
    voiceModeRef.current = voiceMode;
  }, [voiceMode]);

  React.useEffect(() => {
    vadThresholdRef.current = vadThreshold;
  }, [vadThreshold]);

  React.useEffect(() => {
    interruptSensitivityRef.current = interruptSensitivity;
    if (typeof window !== "undefined") {
      window.localStorage.setItem("vgm_interrupt_sensitivity", interruptSensitivity);
    }
  }, [interruptSensitivity]);

  React.useEffect(() => {
    gmSpeakingRef.current = gmSpeaking;
  }, [gmSpeaking]);

  React.useEffect(() => {
    activeUserIdRef.current = draft?.voice.active_player_id || draft?.voice.player_id || "player1";
  }, [draft?.voice.active_player_id, draft?.voice.player_id]);

  React.useEffect(() => {
    autoSpeakerRef.current = draft?.voice.auto_select_active_speaker !== false;
  }, [draft?.voice.auto_select_active_speaker]);

  const saveSettingsMutation = useMutation({
    mutationFn: async (settings: AppSettings) => updateSettings(settings),
    onSuccess: (payload) => {
      if (!payload.settings) return;
      const next = ensurePlayerProfiles(cloneSettings(payload.settings));
      setDraft(next);
      setDirty(false);
      queryClient.setQueryData(["settings"], next);
      queryClient.invalidateQueries({ queryKey: ["status"] });
      queryClient.invalidateQueries({ queryKey: ["kb-docs"] });
      if (payload.rulebook_sync && (payload.rulebook_sync as { ok?: boolean }).ok === false) {
        const msg = String((payload.rulebook_sync as { error?: string }).error || "rulebook sync failed");
        toast.error(msg);
      } else {
        toast.success("Settings saved");
      }
      if (payload.restart_required) {
        toast.message("Some voice provider changes still require process restart.");
      }
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const saveSecretsMutation = useMutation({
    mutationFn: updateSecrets,
    onSuccess: (payload) => {
      queryClient.setQueryData(["secrets"], payload);
      queryClient.invalidateQueries({ queryKey: ["status"] });
      setSecretValues({
        OPENAI_API_KEY: "",
        DEEPGRAM_API_KEY: "",
        ELEVENLABS_API_KEY: "",
        OPENAI_BASE_URL: "",
      });
      setSecretActions({
        OPENAI_API_KEY: "keep",
        DEEPGRAM_API_KEY: "keep",
        ELEVENLABS_API_KEY: "keep",
        OPENAI_BASE_URL: "keep",
      });
      toast.success("Secrets saved to .env and live process environment");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const setupSearchMutation = useMutation({
    mutationFn: (query: string) => searchSystems(query),
    onSuccess: (results) => {
      setSetupHints(results);
      setSelectedHintId(results[0]?.id || "");
      if (!results.length) {
        toast.message("No hints found. Try a broader game system name.");
      }
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      setUploadPct(0);
      const base = slugify(file.name.replace(/\.pdf$/i, "") || "rulebook");
      return uploadPdfInChunks(file, {
        docId: `${base}_${Date.now().toString().slice(-5)}`,
        ruleset: draft?.knowledge.primary_rulebook_ruleset || undefined,
        docKind: draft?.knowledge.primary_rulebook_doc_kind || "rulebook",
        collectionTarget: draft?.knowledge.primary_rulebook_collection_target || "game",
        onProgress: (done, total) => setUploadPct(Math.floor((done / Math.max(1, total)) * 100)),
      });
    },
    onSuccess: async (payload) => {
      setRulebookFile(null);
      setUploadPct(100);
      const next = cloneSettings(draft as AppSettings);
      next.knowledge.primary_rulebook_source = "doc";
      next.knowledge.primary_rulebook_doc_choice = payload.doc_id;
      next.knowledge.primary_rulebook_doc_id = payload.doc_id;
      next.knowledge.active_doc_ids = Array.from(
        new Set([...(next.knowledge.active_doc_ids || []), payload.doc_id])
      );
      next.knowledge.enabled = true;
      setDraft(next);
      setDirty(true);
      await queryClient.invalidateQueries({ queryKey: ["kb-docs"] });
      saveSettingsMutation.mutate(next);
      toast.success(`Uploaded ${payload.doc_id} and queued settings sync`);
    },
    onError: (e: Error) => {
      setUploadPct(0);
      toast.error(e.message);
    },
  });

  const syncRulebookMutation = useMutation({
    mutationFn: () => syncRulebook({}),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["kb-docs"] });
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
      toast.success("Rulebook synced");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const ingestMutation = useMutation({
    mutationFn: (docId: string) => ingestDoc({ doc_id: docId }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["kb-docs"] });
      toast.success("Document ingested");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const deleteDocMutation = useMutation({
    mutationFn: (docId: string) => deleteDoc({ doc_id: docId }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["kb-docs"] });
      toast.success("Document removed");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const kbSearchMutation = useMutation({
    mutationFn: (query: string) =>
      kbSearch({
        query,
        top_k: draft?.knowledge.top_k || 5,
      }),
    onSuccess: (payload) => {
      setKbResults(payload.results || []);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const clearMemoryMutation = useMutation({
    mutationFn: () =>
      clearMemory({
        scope: memoryScope,
        session_id: draft?.voice.session_id,
        player_id: draft?.voice.active_player_id || draft?.voice.player_id,
      }),
    onSuccess: async (payload) => {
      await queryClient.invalidateQueries({ queryKey: ["memory"] });
      toast.success(`Cleared ${payload.cleared} memory rows`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const campaignNewMutation = useMutation({
    mutationFn: () => newCampaign(),
    onSuccess: (payload) => {
      setDraft(ensurePlayerProfiles(cloneSettings(payload.settings)));
      setDirty(false);
      queryClient.setQueryData(["settings"], payload.settings);
      queryClient.invalidateQueries({ queryKey: ["memory"] });
      clearMessages();
      toast.success(`New campaign: ${payload.campaign?.id || "created"}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const campaignResumeMutation = useMutation({
    mutationFn: resumeLatestCampaign,
    onSuccess: (payload) => {
      setDraft(ensurePlayerProfiles(cloneSettings(payload.settings)));
      setDirty(false);
      queryClient.setQueryData(["settings"], payload.settings);
      queryClient.invalidateQueries({ queryKey: ["memory"] });
      toast.success(`Resumed campaign ${payload.campaign?.id || ""}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const campaignResetMutation = useMutation({
    mutationFn: resetCampaign,
    onSuccess: (payload) => {
      queryClient.invalidateQueries({ queryKey: ["memory"] });
      clearMessages();
      toast.success(`Campaign reset (${payload.cleared_memory_entries} memory rows)`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const updateDraft = React.useCallback((mutator: (next: AppSettings) => void) => {
    setDraft((prev) => {
      if (!prev) return prev;
      const next = cloneSettings(prev);
      mutator(next);
      return ensurePlayerProfiles(next);
    });
    setDirty(true);
  }, []);

  const autoSelectDetectedSpeaker = React.useCallback(
    (rawUserId: unknown, source: string) => {
      const detected = String(rawUserId || "").trim();
      if (!detected) return;
      let changed = false;
      setDraft((prev) => {
        if (!prev) return prev;
        if (!prev.voice.auto_select_active_speaker) return prev;
        const profiles = Array.isArray(prev.voice.player_profiles) ? prev.voice.player_profiles : [];
        if (!profiles.some((p) => p.player_id === detected)) return prev;
        if (prev.voice.active_player_id === detected && prev.voice.player_id === detected) return prev;
        const next = cloneSettings(prev);
        next.voice.active_player_id = detected;
        next.voice.player_id = detected;
        changed = true;
        return next;
      });
      if (changed) {
        addTrace("info", "active_speaker_auto_selected", { player_id: detected, source });
      }
    },
    [addTrace]
  );

  const onSaveSettings = React.useCallback(async () => {
    if (!draft) return;
    await saveSettingsMutation.mutateAsync(ensurePlayerProfiles(cloneSettings(draft)));
  }, [draft, saveSettingsMutation]);

  const applyHint = React.useCallback(() => {
    if (!draft || !setupHints.length) return;
    const hint = setupHints.find((h) => h.id === selectedHintId) || setupHints[0];
    if (!hint) return;

    updateDraft((next) => {
      const ruleset = String(hint.ruleset || hint.setup_system || hint.id || "generic").trim().toLowerCase();
      next.knowledge.primary_rulebook_ruleset = ruleset;
      if (!next.knowledge.primary_rulebook_doc_id) {
        next.knowledge.primary_rulebook_doc_id = `${ruleset}_core`;
      }
      const hintLine = String(hint.beginner_hint || "").trim();
      if (hintLine) {
        next.prompts.resolve_system = `${next.prompts.resolve_system}\nStyle: ${hintLine}`.slice(0, 6000);
      }
    });
    toast.success(`Applied hint: ${hint.name}`);
  }, [draft, selectedHintId, setupHints, updateDraft]);

  const getAudioContext = React.useCallback(async () => {
    if (!audioCtxRef.current) {
      audioCtxRef.current = new AudioContext();
    }
    if (audioCtxRef.current.state === "suspended") {
      try {
        await audioCtxRef.current.resume();
      } catch {
        // ignore
      }
    }
    return audioCtxRef.current;
  }, []);

  const clearAudioPlaybackQueue = React.useCallback(() => {
    playheadRef.current = 0;
    const active = activeOutSourcesRef.current;
    if (!active.size) return;
    for (const source of Array.from(active)) {
      try {
        source.onended = null;
        source.stop(0);
      } catch {
        // ignore
      }
      try {
        source.disconnect();
      } catch {
        // ignore
      }
    }
    active.clear();
  }, []);

  const playAudioOut = React.useCallback(
    async (audioBase64: string, sampleRateRaw: number) => {
      const audioBytes = base64ToBytes(audioBase64);
      if (!audioBytes.length) return;
      const pcm = pcm16BytesToFloat32(audioBytes);
      if (!pcm.length) return;

      const ctx = await getAudioContext();
      const sourceRate = Number.isFinite(sampleRateRaw) && sampleRateRaw > 0 ? sampleRateRaw : 24000;
      const out = resampleLinear(pcm, sourceRate, ctx.sampleRate);
      if (!out.length) return;

      const buffer = ctx.createBuffer(1, out.length, ctx.sampleRate);
      buffer.getChannelData(0).set(out);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      const active = activeOutSourcesRef.current;
      active.add(source);
      source.onended = () => {
        active.delete(source);
        try {
          source.disconnect();
        } catch {
          // ignore
        }
      };

      const now = ctx.currentTime;
      const startAt = Math.max(now, playheadRef.current || now);
      source.start(startAt);
      playheadRef.current = startAt + buffer.duration;

      recvAudioFramesRef.current += 1;
      ttsTurnAudioFramesRef.current += 1;
      ttsTurnAudioBytesRef.current += audioBytes.length;
      const nowMs = Date.now();
      if (nowMs - lastAudioTraceAtRef.current > 1500) {
        lastAudioTraceAtRef.current = nowMs;
        addTrace("info", "audio_out_playback", {
          recv_frames: recvAudioFramesRef.current,
          sample_rate: sampleRateRaw,
          bytes: audioBytes.length,
        });
      }
    },
    [addTrace, getAudioContext]
  );

  const sendAudioFrame = React.useCallback((bytes: Uint8Array) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !bytes.length) return;
    const autoSpeaker = voiceModeRef.current === "auto" || autoSpeakerRef.current;
    const wireUserId = autoSpeaker ? "" : activeUserIdRef.current || "player1";
    const payload: Record<string, unknown> = {
      type: "audio_in",
      sample_rate: 16000,
      num_channels: 1,
      audio: bytesToBase64(bytes),
    };
    if (wireUserId) {
      payload.user_id = wireUserId;
    }
    ws.send(JSON.stringify(payload));
    sentAudioFramesRef.current += 1;
    const nowMs = Date.now();
    if (nowMs - lastAudioTraceAtRef.current > 1500) {
      lastAudioTraceAtRef.current = nowMs;
      addTrace("info", "audio_in_sent", {
        sent_frames: sentAudioFramesRef.current,
        bytes: bytes.length,
        user_id: wireUserId || "<auto>",
      });
    }
  }, [addTrace]);

  const flushSendBuffer = React.useCallback(() => {
    const chunk = sendBufferRef.current;
    if (!chunk.length) return;
    sendBufferRef.current = new Uint8Array(0);
    sendAudioFrame(chunk);
  }, [sendAudioFrame]);

  const enqueueSendBytes = React.useCallback(
    (chunk: Uint8Array) => {
      if (!chunk.length) return;
      sendBufferRef.current = concatBytes(sendBufferRef.current, chunk);
      if (sendBufferRef.current.length >= 3200) {
        flushSendBuffer();
      }
    },
    [flushSendBuffer]
  );

  const sendVAD = React.useCallback((state: "start" | "stop", voiceprint?: Voiceprint) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const payload: Record<string, unknown> = { type: "vad", state, gm_speaking: gmSpeakingRef.current };
    if (state === "stop" && voiceprint) {
      payload.voiceprint = voiceprint;
    }
    ws.send(JSON.stringify(payload));
  }, []);

  const startTalk = React.useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (talkRef.current) return;
    // Barge-in UX: if GM audio is currently playing, drop queued local playback first.
    if (gmSpeakingRef.current) {
      clearAudioPlaybackQueue();
    }
    talkRef.current = true;
    setIsTalking(true);
    sendBufferRef.current = new Uint8Array(0);
    vadStartCandidateAtRef.current = null;
    vadStopCandidateAtRef.current = null;
    utteranceSamplesRef.current = 0;
    utteranceZeroCrossRef.current = 0;
    utterancePitchSumRef.current = 0;
    utterancePitchWeightRef.current = 0;
    utteranceChunkCountRef.current = 0;
    sendVAD("start");
    addTrace("info", "vad_start", {
      mode: voiceModeRef.current,
      sensitivity: interruptSensitivityRef.current,
      gm_speaking: gmSpeakingRef.current,
    });
  }, [addTrace, clearAudioPlaybackQueue, sendVAD]);

  const stopTalk = React.useCallback(() => {
    if (!talkRef.current) return;
    talkRef.current = false;
    setIsTalking(false);
    vadStartCandidateAtRef.current = null;
    vadStopCandidateAtRef.current = null;
    flushSendBuffer();
    let voiceprint: Voiceprint | undefined;
    const samples = utteranceSamplesRef.current;
    if (samples >= 1600) {
      const zcr = utteranceZeroCrossRef.current / samples;
      const pitch_hz =
        utterancePitchWeightRef.current > 0
          ? utterancePitchSumRef.current / utterancePitchWeightRef.current
          : 0;
      voiceprint = {
        pitch_hz: Number.isFinite(pitch_hz) ? pitch_hz : 0,
        zcr: Number.isFinite(zcr) ? zcr : 0,
      };
      addTrace("info", "voiceprint_captured", voiceprint);
    }
    sendVAD("stop", voiceprint);
    addTrace("info", "vad_stop", { mode: voiceModeRef.current });
  }, [addTrace, flushSendBuffer, sendVAD]);

  const autoVADTick = React.useCallback(
    (level: number) => {
      if (voiceModeRef.current !== "auto") return;
      const now = performance.now();
      const sensitivity = interruptSensitivityRef.current;
      let startMs = 140;
      let stopMs = 650;
      let startFactor = 1.0;

      if (sensitivity === "high") {
        startMs = 90;
        stopMs = 450;
        startFactor = 0.9;
      } else if (sensitivity === "max") {
        startMs = 60;
        stopMs = 320;
        startFactor = 0.82;
      }

      // During GM speech we want barge-in to trigger faster.
      if (gmSpeakingRef.current) {
        startMs = Math.max(32, Math.floor(startMs * 0.6));
        stopMs = Math.max(180, Math.floor(stopMs * 0.7));
        startFactor *= 0.82;
      }

      // Speaker diarization improves with slightly longer captured utterances.
      // Keep this only for auto speaker mode while GM is not speaking.
      if (autoSpeakerRef.current && !gmSpeakingRef.current) {
        startMs = Math.max(startMs, 110);
        stopMs = Math.max(stopMs, 820);
      }

      const startTh = Math.max(0.003, vadThresholdRef.current * startFactor);
      const stopTh = startTh * (sensitivity === "max" || gmSpeakingRef.current ? 0.72 : 0.6);

      if (!talkRef.current) {
        vadStopCandidateAtRef.current = null;
        if (level > startTh) {
          if (vadStartCandidateAtRef.current === null) vadStartCandidateAtRef.current = now;
          if (now - vadStartCandidateAtRef.current >= startMs) {
            startTalk();
            vadStartCandidateAtRef.current = null;
          }
        } else {
          vadStartCandidateAtRef.current = null;
        }
        return;
      }

      vadStartCandidateAtRef.current = null;
      if (level < stopTh) {
        if (vadStopCandidateAtRef.current === null) vadStopCandidateAtRef.current = now;
        if (now - vadStopCandidateAtRef.current >= stopMs) {
          stopTalk();
          vadStopCandidateAtRef.current = null;
        }
      } else {
        vadStopCandidateAtRef.current = null;
      }
    },
    [startTalk, stopTalk]
  );

  const stopMicProcessing = React.useCallback(() => {
    addTrace("info", "mic_stop");
    stopTalk();

    if (micProcessorRef.current) {
      try {
        micProcessorRef.current.disconnect();
      } catch {
        // ignore
      }
      micProcessorRef.current.onaudioprocess = null;
      micProcessorRef.current = null;
    }
    if (micSourceRef.current) {
      try {
        micSourceRef.current.disconnect();
      } catch {
        // ignore
      }
      micSourceRef.current = null;
    }
    if (micStreamRef.current) {
      for (const track of micStreamRef.current.getTracks()) {
        track.stop();
      }
      micStreamRef.current = null;
    }
    sendBufferRef.current = new Uint8Array(0);
    setVadLevel(0);
    setMicState("idle");
  }, [addTrace, stopTalk]);

  const startMicProcessing = React.useCallback(async () => {
    if (micProcessorRef.current) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      setMicState("error");
      toast.error("Microphone API is not available in this browser.");
      addTrace("error", "mic_api_unavailable");
      return;
    }

    try {
      addTrace("info", "mic_start_begin");
      const ctx = await getAudioContext();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      micStreamRef.current = stream;

      const source = ctx.createMediaStreamSource(stream);
      micSourceRef.current = source;

      const proc = ctx.createScriptProcessor(2048, 1, 1);
      micProcessorRef.current = proc;
      let smooth = 0;

      proc.onaudioprocess = (ev) => {
        const input = ev.inputBuffer.getChannelData(0);
        if (!input || !input.length) return;

        let sum = 0;
        for (let i = 0; i < input.length; i += 1) {
          const v = input[i];
          sum += v * v;
        }
        const rms = Math.sqrt(sum / input.length);
        smooth = smooth * 0.85 + rms * 0.15;
        setVadLevel(smooth);

        autoVADTick(smooth);
        if (!talkRef.current) return;

        utteranceSamplesRef.current += input.length;
        utteranceChunkCountRef.current += 1;
        for (let i = 1; i < input.length; i += 1) {
          const a = input[i - 1];
          const b = input[i];
          if ((a >= 0 && b < 0) || (a < 0 && b >= 0)) {
            utteranceZeroCrossRef.current += 1;
          }
        }
        if (utteranceChunkCountRef.current % 3 === 0 && rms > 0.01) {
          const pitch = estimatePitchHz(input, ev.inputBuffer.sampleRate);
          if (pitch > 50 && pitch < 450) {
            const w = Math.max(0.01, rms);
            utterancePitchSumRef.current += pitch * w;
            utterancePitchWeightRef.current += w;
          }
        }

        const pcm = downsampleToPCM16(input, ev.inputBuffer.sampleRate, 16000);
        if (!pcm.length) return;
        enqueueSendBytes(pcm);
      };

      source.connect(proc);
      // ScriptProcessor must be connected to run in most browsers.
      proc.connect(ctx.destination);
      setMicState("ready");
      addTrace("info", "mic_start_ready", { sample_rate: ctx.sampleRate });
    } catch (e) {
      setMicState("error");
      toast.error(`Microphone error: ${e instanceof Error ? e.message : String(e)}`);
      addTrace("error", "mic_start_error", { error: e instanceof Error ? e.message : String(e) });
    }
  }, [addTrace, autoVADTick, enqueueSendBytes, getAudioContext]);

  const connectWS = React.useCallback(() => {
    const target = wsUrl.trim();
    if (!target) {
      toast.error("WebSocket URL is empty");
      addTrace("error", "ws_connect_empty_url");
      return;
    }
    addTrace("info", "ws_connect_begin", { url: target });
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.close(1000, "reconnect");
    }
    setWsStatus("connecting");

    const socket = new WebSocket(target);
    wsRef.current = socket;

    socket.onopen = () => {
      setWsStatus("connected");
      setGmSpeaking(false);
      clearAudioPlaybackQueue();
      void getAudioContext();
      void startMicProcessing();
      addTrace("info", "ws_open", { url: target });
      addMessage({
        id: makeId(),
        role: "system",
        speaker: "SYSTEM",
        text: "WebSocket connected.",
        ts: Date.now(),
      });
    };

    socket.onclose = (ev) => {
      setWsStatus("disconnected");
      setGmSpeaking(false);
      clearAudioPlaybackQueue();
      stopMicProcessing();
      addTrace("warn", "ws_close", {
        code: typeof ev?.code === "number" ? ev.code : null,
        reason: ev?.reason ? String(ev.reason) : "",
      });
      addMessage({
        id: makeId(),
        role: "system",
        speaker: "SYSTEM",
        text: "WebSocket disconnected.",
        ts: Date.now(),
      });
    };

    socket.onerror = () => {
      setWsStatus("disconnected");
      setGmSpeaking(false);
      clearAudioPlaybackQueue();
      stopMicProcessing();
      addTrace("error", "ws_error");
      addMessage({
        id: makeId(),
        role: "error",
        speaker: "ERROR",
        text: "WebSocket error.",
        ts: Date.now(),
      });
    };

    socket.onmessage = (ev) => {
      let msg: Record<string, unknown> | null = null;
      try {
        msg = JSON.parse(ev.data as string) as Record<string, unknown>;
      } catch {
        addTrace("warn", "ws_message_parse_error");
        return;
      }
      if (!msg || !msg.type) return;
      const type = String(msg.type);
      const rtviData =
        msg.data && typeof msg.data === "object" ? (msg.data as Record<string, unknown>) : null;
      if (type !== "audio_out") {
        addTrace("info", "ws_message", {
          type,
          event: String(msg.event || ""),
        });
      }

      if (type === "audio_out") {
        void playAudioOut(String(msg.audio || ""), Number(msg.sample_rate || 24000));
        return;
      }
      if (type === "transcript" && msg.finalized) {
        const lang = String(msg.language || "").trim();
        const langSource = String(msg.language_source || "").trim();
        const langMeta = lang
          ? ` [${lang}${langSource ? `/${langSource}` : ""}]`
          : "";
        addMessage({
          id: makeId(),
          role: "player",
          speaker: String(msg.user_id || "PLAYER") + langMeta,
          text: String(msg.text || ""),
          ts: Date.now(),
        });
        autoSelectDetectedSpeaker(msg.user_id, "transcript");
        return;
      }
      if (type === "user-transcription" && rtviData) {
        const finalized = rtviData.final == null ? true : Boolean(rtviData.final);
        if (!finalized) return;
        addMessage({
          id: makeId(),
          role: "player",
          speaker: String(rtviData.user_id || "PLAYER"),
          text: String(rtviData.text || ""),
          ts: Date.now(),
        });
        autoSelectDetectedSpeaker(rtviData.user_id, "user-transcription");
        return;
      }
      if (type === "text") {
        addMessage({
          id: makeId(),
          role: "gm",
          speaker: "GM",
          text: String(msg.text || ""),
          ts: Date.now(),
        });
        return;
      }
      if (type === "bot-started-speaking") {
        setGmSpeaking(true);
        addTrace("info", "gm_speaking", { state: true });
        return;
      }
      if (type === "bot-stopped-speaking") {
        setGmSpeaking(false);
        addTrace("info", "gm_speaking", {
          state: false,
          tts_audio_frames: ttsTurnAudioFramesRef.current,
          tts_audio_bytes: ttsTurnAudioBytesRef.current,
        });
        return;
      }
      if (type === "bot-tts-started") {
        ttsTurnAudioFramesRef.current = 0;
        ttsTurnAudioBytesRef.current = 0;
      }
      if (type === "bot-output" && rtviData) {
        addTrace("info", "rtvi_bot_output", rtviData);
        return;
      }
      if (type === "bot-tts-text" || type === "bot-llm-text") {
        addTrace("info", "rtvi_text_stream", rtviData ?? msg);
        return;
      }
      if (type === "debug") {
        addTrace("info", "server_debug", msg);
        return;
      }
      if (type === "turn_debug") {
        addTrace("info", "turn_debug", msg.debug ?? msg);
        return;
      }
      if (type === "error") {
        const rtviErr =
          rtviData && typeof rtviData === "object"
            ? String(
                (rtviData.error as unknown) ||
                  (rtviData.message as unknown) ||
                  (rtviData.reason as unknown) ||
                  ""
              )
            : "";
        const errText = String(msg.error || rtviErr || "unknown error");
        addTrace("error", "server_error", { error: errText });
        addMessage({
          id: makeId(),
          role: "error",
          speaker: "ERROR",
          text: errText,
          ts: Date.now(),
        });
        return;
      }
      if (type === "speaker_map") {
        addMessage({
          id: makeId(),
          role: "system",
          speaker: "SYSTEM",
          text: `Speaker mapped: ${String(msg.speaker_raw_id || "?")} -> ${String(msg.player_id || "?")}`,
          ts: Date.now(),
        });
        autoSelectDetectedSpeaker(msg.player_id, "speaker_map");
        return;
      }
      if (type === "settings" && msg.settings) {
        const next = ensurePlayerProfiles(cloneSettings(msg.settings as AppSettings));
        setDraft(next);
        setDirty(false);
        queryClient.setQueryData(["settings"], next);
      }
    };
  }, [
    addTrace,
    addMessage,
    autoSelectDetectedSpeaker,
    clearAudioPlaybackQueue,
    getAudioContext,
    playAudioOut,
    queryClient,
    setWsStatus,
    startMicProcessing,
    stopMicProcessing,
    wsUrl,
  ]);

  const disconnectWS = React.useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close(1000, "manual");
      wsRef.current = null;
    }
    stopMicProcessing();
    setGmSpeaking(false);
    clearAudioPlaybackQueue();
    setWsStatus("disconnected");
    addTrace("info", "ws_disconnect_manual");
  }, [addTrace, clearAudioPlaybackQueue, setWsStatus, stopMicProcessing]);

  const sendManual = React.useCallback(() => {
    const text = manualText.trim();
    const ws = wsRef.current;
    if (!text) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      toast.error("WebSocket is not connected");
      addTrace("warn", "manual_send_without_ws");
      return;
    }
    const userId = draft?.voice.active_player_id || draft?.voice.player_id || "player";
    ws.send(
      JSON.stringify({
        type: "manual_transcript",
        text,
        finalized: true,
        user_id: userId,
      })
    );
    addTrace("info", "manual_transcript_sent", { user_id: userId, chars: text.length });
    setManualText("");
  }, [addTrace, draft?.voice.active_player_id, draft?.voice.player_id, manualText]);

  React.useEffect(() => {
    if (voiceMode === "ptt" && talkRef.current) {
      stopTalk();
    }
  }, [voiceMode, stopTalk]);

  React.useEffect(() => {
    const onBlur = () => stopTalk();
    const onVisibility = () => {
      if (document.hidden) stopTalk();
    };
    window.addEventListener("blur", onBlur);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [stopTalk]);

  React.useEffect(() => {
    return () => {
      stopMicProcessing();
      clearAudioPlaybackQueue();
      if (wsRef.current) wsRef.current.close(1000, "unmount");
      if (audioCtxRef.current) {
        void audioCtxRef.current.close();
        audioCtxRef.current = null;
      }
    };
  }, [clearAudioPlaybackQueue, stopMicProcessing]);

  const selectedHint = setupHints.find((h) => h.id === selectedHintId);

  const secretSummary = secretsQuery.data?.secrets || {};
  const docs = docsQuery.data || [];
  const activeDocIds = draft?.knowledge.active_doc_ids || [];
  const traceText = React.useMemo(
    () =>
      traceEntries
        .map((e) => {
          const ts = new Date(e.ts).toISOString();
          const data = safeTraceData(e.data);
          return `${ts} [${e.level.toUpperCase()}] ${e.msg}${data ? ` ${data}` : ""}`;
        })
        .join("\n"),
    [traceEntries]
  );

  const renderTab = () => {
    if (!draft) {
      return (
        <Card>
          <CardContent className="py-16 text-center">
            <LoaderCircle className="mx-auto mb-3 size-6 animate-spin text-sky-600" />
            <div className="text-sm text-slate-600">Loading settings...</div>
          </CardContent>
        </Card>
      );
    }

    if (activeTab === "setup") {
      return (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Sparkles className="size-4 text-sky-600" />
                One-Step Game Setup
              </CardTitle>
              <CardDescription>
                Select system + rulebook + voice settings in one place, then save. No websocket connect required.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <Label>Game System Search</Label>
                <div className="flex gap-2">
                  <Input
                    value={setupQuery}
                    onChange={(e) => setSetupQuery(e.target.value)}
                    placeholder="Numenera, Vagabond, Blades in the Dark..."
                  />
                  <Button
                    variant="secondary"
                    onClick={() => setupSearchMutation.mutate(setupQuery)}
                    disabled={!setupQuery.trim() || setupSearchMutation.isPending}
                  >
                    {setupSearchMutation.isPending ? (
                      <LoaderCircle className="size-4 animate-spin" />
                    ) : (
                      <Search className="size-4" />
                    )}
                    Search
                  </Button>
                </div>
              </div>

              <div>
                <Label>Hints</Label>
                <Select value={selectedHintId} onChange={(e) => setSelectedHintId(e.target.value)}>
                  {setupHints.length === 0 && <option value="">No hint loaded</option>}
                  {setupHints.map((hint) => (
                    <option key={hint.id} value={hint.id}>
                      {hint.name}
                    </option>
                  ))}
                </Select>
                {selectedHint ? (
                  <p className="mt-2 text-xs text-slate-600">
                    {selectedHint.beginner_hint || selectedHint.summary || "No extra hint."}
                  </p>
                ) : null}
                <div className="mt-2">
                  <Button variant="secondary" onClick={applyHint} disabled={!selectedHint}>
                    Apply Hint
                  </Button>
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label>Rulebook Source</Label>
                  <Select
                    value={draft.knowledge.primary_rulebook_source}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.knowledge.primary_rulebook_source = e.target.value as "path" | "doc";
                      })
                    }
                  >
                    <option value="doc">Uploaded / Dropdown</option>
                    <option value="path">Server Path</option>
                  </Select>
                </div>
                <div>
                  <Label>Ruleset Tag</Label>
                  <Input
                    value={draft.knowledge.primary_rulebook_ruleset}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.knowledge.primary_rulebook_ruleset = e.target.value;
                      })
                    }
                    placeholder="numenera / dnd5e / custom"
                  />
                </div>
              </div>

              {draft.knowledge.primary_rulebook_source === "doc" ? (
                <div>
                  <Label>Uploaded Rulebook</Label>
                  <Select
                    value={draft.knowledge.primary_rulebook_doc_choice || ""}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.knowledge.primary_rulebook_doc_choice = e.target.value;
                        next.knowledge.primary_rulebook_doc_id = e.target.value;
                      })
                    }
                  >
                    {!docs.length && <option value="">No uploaded documents yet</option>}
                    {docs.map((doc) => (
                      <option key={doc.doc_id} value={doc.doc_id}>
                        {doc.doc_id} ({doc.status || "uploaded"})
                      </option>
                    ))}
                  </Select>
                </div>
              ) : (
                <div>
                  <Label>Rulebook Path</Label>
                  <Input
                    value={draft.knowledge.primary_rulebook_path}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.knowledge.primary_rulebook_path = e.target.value;
                      })
                    }
                    placeholder="Numenera.pdf"
                  />
                </div>
              )}

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label>Auto Ingest</Label>
                  <Select
                    value={String(draft.knowledge.primary_rulebook_auto_ingest)}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.knowledge.primary_rulebook_auto_ingest = e.target.value === "true";
                      })
                    }
                  >
                    <option value="true">Yes</option>
                    <option value="false">No</option>
                  </Select>
                </div>
                <div>
                  <Label>Auto Activate</Label>
                  <Select
                    value={String(draft.knowledge.primary_rulebook_auto_activate)}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.knowledge.primary_rulebook_auto_activate = e.target.value === "true";
                      })
                    }
                  >
                    <option value="true">Yes</option>
                    <option value="false">No</option>
                  </Select>
                </div>
              </div>

              <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                <Label>Upload New PDF</Label>
                <Input type="file" accept="application/pdf" onChange={(e) => setRulebookFile(e.target.files?.[0] || null)} />
                <div className="flex gap-2">
                  <Button
                    variant="secondary"
                    disabled={!rulebookFile || uploadMutation.isPending}
                    onClick={() => {
                      if (rulebookFile) uploadMutation.mutate(rulebookFile);
                    }}
                  >
                    {uploadMutation.isPending ? (
                      <LoaderCircle className="size-4 animate-spin" />
                    ) : (
                      <CloudUpload className="size-4" />
                    )}
                    Upload + Select
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={syncRulebookMutation.isPending}
                    onClick={() => syncRulebookMutation.mutate()}
                  >
                    {syncRulebookMutation.isPending ? (
                      <LoaderCircle className="size-4 animate-spin" />
                    ) : (
                      <RefreshCw className="size-4" />
                    )}
                    Sync Rulebook
                  </Button>
                </div>
                {(uploadMutation.isPending || uploadPct > 0) && (
                  <div>
                    <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
                      <div
                        className="h-full bg-[linear-gradient(90deg,#14b8a6,#0f62fe)] transition-all"
                        style={{ width: `${uploadPct}%` }}
                      />
                    </div>
                    <div className="mt-1 text-xs text-slate-600">Upload {uploadPct}%</div>
                  </div>
                )}
              </div>

              <div className="flex flex-wrap gap-2 pt-2">
                <Button onClick={onSaveSettings} disabled={saveSettingsMutation.isPending || !dirty}>
                  {saveSettingsMutation.isPending ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : (
                    <Save className="size-4" />
                  )}
                  Save Setup
                </Button>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      if (!settingsQuery.data) return;
                      setDraft(ensurePlayerProfiles(cloneSettings(settingsQuery.data as AppSettings)));
                      setDirty(false);
                    }}
                    disabled={!dirty || !settingsQuery.data}
                  >
                    Revert
                  </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Mic2 className="size-4 text-sky-600" />
                Voice + Players
              </CardTitle>
              <CardDescription>
                Assign providers, language behavior, and up to 8 player identities (with optional voiceprint labels).
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label>Campaign ID</Label>
                  <Input
                    value={draft.voice.campaign_id}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.voice.campaign_id = e.target.value;
                      })
                    }
                  />
                </div>
                <div>
                  <Label>Locale</Label>
                  <Input
                    value={draft.voice.locale}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.voice.locale = e.target.value;
                      })
                    }
                    placeholder="en-US"
                  />
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label>Response Language</Label>
                  <Select
                    value={draft.prompts.response_language_mode}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.prompts.response_language_mode = e.target.value as "player" | "locale";
                      })
                    }
                  >
                    <option value="player">Auto (follow speaking player)</option>
                    <option value="locale">Always use locale</option>
                  </Select>
                </div>
                <div>
                  <Label>Active Player</Label>
                  <Select
                    value={draft.voice.active_player_id}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.voice.active_player_id = e.target.value;
                        next.voice.player_id = e.target.value;
                      })
                    }
                  >
                    {draft.voice.player_profiles.map((p) => (
                      <option key={p.player_id} value={p.player_id}>
                        {p.display_name} ({p.player_id})
                      </option>
                    ))}
                  </Select>
                </div>
              </div>

              <div>
                <Label>Active Speaker Selection</Label>
                <Select
                  value={draft.voice.auto_select_active_speaker ? "auto" : "manual"}
                  onChange={(e) =>
                    updateDraft((next) => {
                      next.voice.auto_select_active_speaker = e.target.value === "auto";
                    })
                  }
                >
                  <option value="auto">Auto (from diarized voice)</option>
                  <option value="manual">Manual only</option>
                </Select>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label>STT Provider</Label>
                  <Select
                    value={draft.openai.stt_provider}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.openai.stt_provider = e.target.value;
                      })
                    }
                  >
                    {draft.openai.stt_provider_options.map((opt) => (
                      <option key={opt} value={opt}>
                        {opt}
                      </option>
                    ))}
                  </Select>
                </div>
                <div>
                  <Label>TTS Provider</Label>
                  <Select
                    value={draft.openai.tts_provider}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.openai.tts_provider = e.target.value;
                      })
                    }
                  >
                    {draft.openai.tts_provider_options.map((opt) => (
                      <option key={opt} value={opt}>
                        {opt}
                      </option>
                    ))}
                  </Select>
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label>STT Model</Label>
                  <Input
                    value={draft.openai.stt_model}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.openai.stt_model = e.target.value;
                      })
                    }
                    list="stt-models"
                  />
                  <datalist id="stt-models">
                    {draft.openai.stt_model_options.map((opt) => (
                      <option key={opt} value={opt} />
                    ))}
                  </datalist>
                </div>
                <div>
                  <Label>TTS Model</Label>
                  <Input
                    value={draft.openai.tts_model}
                    onChange={(e) =>
                      updateDraft((next) => {
                        next.openai.tts_model = e.target.value;
                      })
                    }
                    list="tts-models"
                  />
                  <datalist id="tts-models">
                    {draft.openai.tts_model_options.map((opt) => (
                      <option key={opt} value={opt} />
                    ))}
                  </datalist>
                </div>
              </div>

              {draft.openai.stt_provider === "deepgram" ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <Label>Deepgram STT Feature</Label>
                    <Select
                      value={draft.openai.deepgram_feature_profile || "speaker_diarization"}
                      onChange={(e) =>
                        updateDraft((next) => {
                          next.openai.deepgram_feature_profile = e.target.value;
                          // Feature presets are tuned for Nova-3.
                          next.openai.stt_model = "nova-3-general";
                        })
                      }
                    >
                      {(
                        draft.openai.deepgram_feature_profile_options?.length
                          ? draft.openai.deepgram_feature_profile_options
                          : deepgramFeaturePresets.map((x) => x.id)
                      ).map((opt) => (
                        <option key={opt} value={opt}>
                          {deepgramPresetLabel(opt)}
                        </option>
                      ))}
                    </Select>
                  </div>
                  <div>
                    <Label>Preset Mapping</Label>
                    <Input
                      value={deepgramPresetSummary(draft.openai.deepgram_feature_profile || "speaker_diarization")}
                      readOnly
                    />
                  </div>
                </div>
              ) : null}

              <div>
                <Label>TTS Voice / Voice ID</Label>
                <Input
                  value={draft.openai.tts_voice}
                  onChange={(e) =>
                    updateDraft((next) => {
                      next.openai.tts_voice = e.target.value;
                    })
                  }
                  placeholder="Paste ElevenLabs voice_id or OpenAI preset"
                />
              </div>

              {draft.openai.tts_provider === "elevenlabs" ? (
                <div>
                  <Label>ElevenLabs Voice Dropdown</Label>
                  <div className="flex gap-2">
                    <Select
                      value={
                        (elevenVoicesQuery.data || []).some((v) => v.voice_id === draft.openai.tts_voice)
                          ? draft.openai.tts_voice
                          : ""
                      }
                      onChange={(e) =>
                        updateDraft((next) => {
                          next.openai.tts_voice = e.target.value;
                        })
                      }
                    >
                      <option value="">Select voice id (optional)</option>
                      {(elevenVoicesQuery.data || []).map((v) => (
                        <option key={v.voice_id} value={v.voice_id}>
                          {v.name} ({v.voice_id})
                        </option>
                      ))}
                    </Select>
                    <Button variant="secondary" onClick={() => elevenVoicesQuery.refetch()}>
                      Refresh
                    </Button>
                  </div>
                </div>
              ) : null}

              <div>
                <Label>Player Profiles (up to 8)</Label>
                <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                  {draft.voice.player_profiles.map((profile, idx) => (
                    <div key={`${profile.player_id}_${idx}`} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                      <Input
                        value={profile.display_name}
                        onChange={(e) =>
                          updateDraft((next) => {
                            next.voice.player_profiles[idx].display_name = e.target.value;
                          })
                        }
                        placeholder="Display name"
                      />
                      <Input
                        value={profile.player_id}
                        onChange={(e) =>
                          updateDraft((next) => {
                            next.voice.player_profiles[idx].player_id = slugify(e.target.value, `player${idx + 1}`);
                          })
                        }
                        placeholder="player id"
                      />
                      <Button
                        variant="ghost"
                        disabled={draft.voice.player_profiles.length <= 1}
                        onClick={() =>
                          updateDraft((next) => {
                            next.voice.player_profiles.splice(idx, 1);
                          })
                        }
                      >
                        Remove
                      </Button>
                    </div>
                  ))}
                  <Button
                    variant="secondary"
                    disabled={draft.voice.player_profiles.length >= 8}
                    onClick={() =>
                      updateDraft((next) => {
                        const n = next.voice.player_profiles.length + 1;
                        next.voice.player_profiles.push({
                          player_id: `player${n}`,
                          display_name: `Player ${n}`,
                          voice_profile: "",
                        });
                      })
                    }
                  >
                    Add Player
                  </Button>
                </div>
              </div>

              <div>
                <Label>LLM Provider</Label>
                <Select
                  value={draft.openai.llm_provider || "openai"}
                  onChange={(e) =>
                    updateDraft((next) => {
                      next.openai.llm_provider = e.target.value;
                    })
                  }
                >
                  {(draft.openai.llm_provider_options?.length
                    ? draft.openai.llm_provider_options
                    : ["openai", "codex_chatgpt"]
                  ).map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </Select>
                {String(draft.openai.llm_provider || "openai") === "codex_chatgpt" ? (
                  <p className="mt-1 text-xs text-slate-500">
                    Uses local <code>codex login</code> session (ChatGPT account). STT/TTS providers still require their own credentials.
                  </p>
                ) : null}
              </div>

              <div>
                <Label>LLM Model</Label>
                <Input
                  value={draft.openai.llm_model}
                  onChange={(e) =>
                    updateDraft((next) => {
                      next.openai.llm_model = e.target.value;
                    })
                  }
                  list="llm-models"
                />
                <datalist id="llm-models">
                  {draft.openai.llm_model_options.map((opt) => (
                    <option key={opt} value={opt} />
                  ))}
                </datalist>
              </div>

              <Button onClick={onSaveSettings} disabled={saveSettingsMutation.isPending || !dirty}>
                {saveSettingsMutation.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
                Save Voice + Players
              </Button>
            </CardContent>
          </Card>

          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <KeyRound className="size-4 text-sky-600" />
                API Keys (Editable Without Connect)
              </CardTitle>
              <CardDescription>
                Changes are written to <code>.env</code> and process environment immediately. Some live STT/TTS sessions may still require restart.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 lg:grid-cols-2">
              {secretFields.map((field) => {
                const rec = secretSummary[field.key];
                const action = secretActions[field.key] || "keep";
                return (
                  <div key={field.key} className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                    <div className="flex items-center justify-between">
                      <Label className="mb-0">{field.label}</Label>
                      <Badge className={cn(rec?.present ? "border-emerald-300 text-emerald-700" : "")}>{rec?.present ? rec.masked || "set" : "not set"}</Badge>
                    </div>
                    <Select
                      value={action}
                      onChange={(e) =>
                        setSecretActions((prev) => ({ ...prev, [field.key]: e.target.value as SecretAction }))
                      }
                    >
                      <option value="keep">Keep current</option>
                      <option value="set">Set / update</option>
                      <option value="clear">Clear value</option>
                    </Select>
                    {action === "set" ? (
                      <Input
                        type={field.key.includes("KEY") ? "password" : "text"}
                        value={secretValues[field.key] || ""}
                        onChange={(e) =>
                          setSecretValues((prev) => ({ ...prev, [field.key]: e.target.value }))
                        }
                        placeholder={field.key.includes("KEY") ? "paste secret" : "https://api.openai.com/v1"}
                      />
                    ) : null}
                  </div>
                );
              })}
              <div className="lg:col-span-2">
                <Button
                  onClick={() => {
                    const updates: Record<string, string> = {};
                    const clearKeys: string[] = [];
                    for (const field of secretFields) {
                      const action = secretActions[field.key] || "keep";
                      if (action === "set") {
                        const v = (secretValues[field.key] || "").trim();
                        if (v) updates[field.key] = v;
                      }
                      if (action === "clear") clearKeys.push(field.key);
                    }
                    if (!Object.keys(updates).length && !clearKeys.length) {
                      toast.message("No secret changes to save.");
                      return;
                    }
                    saveSecretsMutation.mutate({
                      updates: Object.keys(updates).length ? updates : undefined,
                      clear_keys: clearKeys.length ? clearKeys : undefined,
                    });
                  }}
                  disabled={saveSecretsMutation.isPending}
                >
                  {saveSecretsMutation.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
                  Save API Keys
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      );
    }

    if (activeTab === "play") {
      return (
        <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Waves className="size-4 text-sky-600" />
                Realtime Control
              </CardTitle>
              <CardDescription>Use websocket for live turns. Manual text input works even without microphone.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <Label>WebSocket URL</Label>
                <Input value={wsUrl} onChange={(e) => setWsUrl(e.target.value)} />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Button onClick={connectWS} disabled={wsStatus === "connecting" || wsStatus === "connected"}>
                  {wsStatus === "connecting" ? <LoaderCircle className="size-4 animate-spin" /> : <Activity className="size-4" />}
                  Connect
                </Button>
                <Button variant="secondary" onClick={disconnectWS} disabled={wsStatus === "disconnected"}>
                  Disconnect
                </Button>
              </div>
              <div className="flex items-center gap-2 text-sm text-slate-600">
                <span
                  className={cn(
                    "inline-block size-2 rounded-full",
                    wsStatus === "connected" ? "bg-emerald-500" : wsStatus === "connecting" ? "bg-amber-500" : "bg-slate-400"
                  )}
                />
                {wsStatus}
              </div>

              <div>
                <Label>Active Speaker</Label>
                <Select
                  value={draft.voice.active_player_id}
                  onChange={(e) =>
                    updateDraft((next) => {
                      next.voice.active_player_id = e.target.value;
                      next.voice.player_id = e.target.value;
                    })
                  }
                >
                  {draft.voice.player_profiles.map((p) => (
                    <option key={p.player_id} value={p.player_id}>
                      {p.display_name} ({p.player_id})
                    </option>
                  ))}
                </Select>
              </div>

              <div>
                <Label>Speaker Selection Mode</Label>
                <Select
                  value={draft.voice.auto_select_active_speaker ? "auto" : "manual"}
                  onChange={(e) =>
                    updateDraft((next) => {
                      next.voice.auto_select_active_speaker = e.target.value === "auto";
                    })
                  }
                >
                  <option value="auto">Auto (from diarized voice)</option>
                  <option value="manual">Manual only</option>
                </Select>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label>Voice Mode</Label>
                  <Select value={voiceMode} onChange={(e) => setVoiceMode(e.target.value as "ptt" | "auto")}>
                    <option value="ptt">Push-To-Talk</option>
                    <option value="auto">Auto VAD</option>
                  </Select>
                </div>
                <div>
                  <Label>Mic Status</Label>
                  <div className="flex h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-700">
                    <span
                      className={cn(
                        "inline-block size-2 rounded-full",
                        micState === "ready" ? "bg-emerald-500" : micState === "error" ? "bg-rose-500" : "bg-slate-400"
                      )}
                    />
                    {micState}
                  </div>
                </div>
              </div>

              <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-3">
                <div className="mb-3 grid gap-3 sm:grid-cols-2">
                  <div>
                    <Label className="text-xs">Interrupt Sensitivity</Label>
                    <Select
                      value={interruptSensitivity}
                      onChange={(e) => setInterruptSensitivity(e.target.value as InterruptSensitivity)}
                    >
                      <option value="balanced">Balanced</option>
                      <option value="high">High</option>
                      <option value="max">Maximum</option>
                    </Select>
                  </div>
                  <div className="pt-5 text-xs text-slate-600">
                    Barge-in status: <span className={gmSpeaking ? "text-emerald-700 font-semibold" : "text-slate-500"}>{gmSpeaking ? "GM speaking" : "idle"}</span>
                  </div>
                </div>
                <div className="mb-1 flex items-center justify-between text-xs text-slate-600">
                  <span>VAD Threshold</span>
                  <span>{vadThreshold.toFixed(3)}</span>
                </div>
                <input
                  type="range"
                  min={0.005}
                  max={0.08}
                  step={0.001}
                  value={vadThreshold}
                  onChange={(e) => setVadThreshold(Number(e.target.value))}
                  className="w-full"
                />
                <div className="mt-3 mb-1 flex items-center justify-between text-xs text-slate-600">
                  <span>VAD Level</span>
                  <span>{vadLevel.toFixed(3)}</span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
                  <div
                    className={cn(
                      "h-full transition-all",
                      isTalking ? "bg-[linear-gradient(90deg,#22c55e,#0ea5e9)]" : "bg-[linear-gradient(90deg,#0ea5e9,#2563eb)]"
                    )}
                    style={{ width: `${Math.max(2, Math.min(100, vadLevel * 1200))}%` }}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <Button
                  variant={isTalking ? "danger" : "secondary"}
                  disabled={wsStatus !== "connected" || voiceMode !== "ptt"}
                  onPointerDown={(e) => {
                    if (voiceMode !== "ptt") return;
                    e.preventDefault();
                    startTalk();
                  }}
                  onPointerUp={(e) => {
                    if (voiceMode !== "ptt") return;
                    e.preventDefault();
                    stopTalk();
                  }}
                  onPointerCancel={() => {
                    if (voiceMode !== "ptt") return;
                    stopTalk();
                  }}
                  onPointerLeave={(e) => {
                    if (voiceMode !== "ptt") return;
                    if (isTalking && e.buttons === 0) stopTalk();
                  }}
                  onKeyDown={(e) => {
                    if (voiceMode !== "ptt") return;
                    if ((e.key === " " || e.key === "Enter") && !e.repeat) {
                      e.preventDefault();
                      startTalk();
                    }
                  }}
                  onKeyUp={(e) => {
                    if (voiceMode !== "ptt") return;
                    if (e.key === " " || e.key === "Enter") {
                      e.preventDefault();
                      stopTalk();
                    }
                  }}
                >
                  {isTalking ? "Talking... release" : "Hold To Talk"}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => {
                    stopMicProcessing();
                    if (wsStatus === "connected") void startMicProcessing();
                  }}
                >
                  Restart Mic
                </Button>
              </div>

              <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-3 text-xs text-slate-600">
                {voiceMode === "auto"
                  ? "Auto VAD is enabled. Speak naturally and the app will send start/stop turns automatically."
                  : "Push-To-Talk mode is enabled. Hold the talk button while speaking."}
              </div>

              <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-3 text-xs text-slate-600">
                Need the original full console too?
                <a className="ml-1 font-semibold text-sky-700 underline" href="/legacy/index.html" target="_blank" rel="noreferrer">
                  /legacy/index.html
                </a>
              </div>

              <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-3">
                <div className="mb-2 flex items-center justify-between">
                  <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-700">
                    <Bug className="size-3.5 text-sky-600" />
                    Voice Trace
                  </div>
                  <label className="flex items-center gap-2 text-xs text-slate-700">
                    <input
                      type="checkbox"
                      checked={traceEnabled}
                      onChange={(e) => setTraceEnabled(e.target.checked)}
                    />
                    enabled
                  </label>
                </div>
                <div className="mb-2 flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => setTraceEntries([])}>
                    Clear
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(traceText || "(empty trace)");
                        toast.success("Trace copied");
                      } catch (e) {
                        toast.error(e instanceof Error ? e.message : String(e));
                      }
                    }}
                  >
                    Copy
                  </Button>
                </div>
                <div className="max-h-40 overflow-y-auto rounded-lg border border-slate-200 bg-white p-2 font-mono text-[11px] text-slate-700">
                  {traceEntries.length === 0 ? (
                    <div className="text-slate-500">No trace entries yet.</div>
                  ) : (
                    <div className="space-y-1">
                      {traceEntries.map((e) => (
                        <div
                          key={e.id}
                          className={cn(
                            "rounded px-1.5 py-1",
                            e.level === "error" && "bg-rose-50 text-rose-700",
                            e.level === "warn" && "bg-amber-50 text-amber-700",
                            e.level === "info" && "bg-slate-50 text-slate-700"
                          )}
                        >
                          {new Date(e.ts).toLocaleTimeString()} [{e.level}] {e.msg}
                          {e.data != null ? ` ${safeTraceData(e.data)}` : ""}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <MessageSquare className="size-4 text-sky-600" />
                GM Conversation
              </CardTitle>
              <CardDescription>Manual transcript input is the fastest way to validate rulebook retrieval and GM behavior.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="h-[460px] overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-3">
                {messages.length === 0 ? (
                  <div className="text-sm text-slate-500">No messages yet.</div>
                ) : (
                  <div className="space-y-2">
                    {messages.map((m) => (
                      <div
                        key={m.id}
                        className={cn(
                          "rounded-xl border px-3 py-2 text-sm",
                          m.role === "gm" && "border-emerald-200 bg-emerald-50",
                          m.role === "player" && "border-sky-200 bg-sky-50",
                          m.role === "system" && "border-slate-200 bg-white",
                          m.role === "error" && "border-rose-200 bg-rose-50"
                        )}
                      >
                        <div className="mb-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{m.speaker}</div>
                        <div>{m.text}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="flex gap-2">
                <Input
                  value={manualText}
                  onChange={(e) => setManualText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      sendManual();
                    }
                  }}
                  placeholder="Type player action or dialogue..."
                />
                <Button onClick={sendManual} disabled={!manualText.trim() || wsStatus !== "connected"}>
                  Send
                </Button>
                <Button variant="ghost" onClick={clearMessages}>
                  Clear
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      );
    }

    return (
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Brain className="size-4 text-sky-600" />
              Knowledge (Qdrant / PDF)
            </CardTitle>
            <CardDescription>
              Advanced controls for retrieval backend, active docs, ingest/delete, and direct searches.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <Label>Knowledge Enabled</Label>
                <Select
                  value={String(draft.knowledge.enabled)}
                  onChange={(e) =>
                    updateDraft((next) => {
                      next.knowledge.enabled = e.target.value === "true";
                    })
                  }
                >
                  <option value="true">Yes</option>
                  <option value="false">No</option>
                </Select>
              </div>
              <div>
                <Label>Backend</Label>
                <Select
                  value={draft.knowledge.backend}
                  onChange={(e) =>
                    updateDraft((next) => {
                      next.knowledge.backend = e.target.value;
                    })
                  }
                >
                  <option value="local">local</option>
                  <option value="remote">remote</option>
                </Select>
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <Label>Top K</Label>
                <Input
                  type="number"
                  min={1}
                  max={20}
                  value={draft.knowledge.top_k}
                  onChange={(e) =>
                    updateDraft((next) => {
                      next.knowledge.top_k = Math.max(1, Math.min(20, Number(e.target.value || 5)));
                    })
                  }
                />
              </div>
              <div>
                <Label>Local Path</Label>
                <Input
                  value={draft.knowledge.local_path}
                  onChange={(e) =>
                    updateDraft((next) => {
                      next.knowledge.local_path = e.target.value;
                    })
                  }
                />
              </div>
            </div>

            <div>
              <Label>Qdrant URL</Label>
              <Input
                value={draft.knowledge.qdrant_url}
                onChange={(e) =>
                  updateDraft((next) => {
                    next.knowledge.qdrant_url = e.target.value;
                  })
                }
              />
            </div>

            <div className="rounded-xl border border-slate-200">
              <div className="grid grid-cols-[1fr_auto_auto_auto] gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-600">
                <div>Document</div>
                <div>Status</div>
                <div>Use</div>
                <div>Actions</div>
              </div>
              <div className="max-h-72 overflow-y-auto">
                {docs.length === 0 ? (
                  <div className="px-3 py-4 text-sm text-slate-500">No docs uploaded yet.</div>
                ) : (
                  docs.map((doc) => {
                    const active = activeDocIds.includes(doc.doc_id);
                    return (
                      <div key={doc.doc_id} className="grid grid-cols-[1fr_auto_auto_auto] items-center gap-2 border-b border-slate-100 px-3 py-2 text-xs">
                        <div className="min-w-0">
                          <div className="truncate font-semibold text-slate-800">{doc.doc_id}</div>
                          <div className="truncate text-slate-500">{doc.filename || doc.path || ""}</div>
                        </div>
                        <Badge>{doc.status || "uploaded"}</Badge>
                        <input
                          type="checkbox"
                          checked={active}
                          onChange={(e) =>
                            updateDraft((next) => {
                              const set = new Set(next.knowledge.active_doc_ids || []);
                              if (e.target.checked) set.add(doc.doc_id);
                              else set.delete(doc.doc_id);
                              next.knowledge.active_doc_ids = Array.from(set);
                            })
                          }
                        />
                        <div className="flex gap-1">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => ingestMutation.mutate(doc.doc_id)}
                            disabled={ingestMutation.isPending}
                          >
                            Ingest
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => deleteDocMutation.mutate(doc.doc_id)}
                            disabled={deleteDocMutation.isPending}
                          >
                            <Trash2 className="size-3.5" />
                          </Button>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button onClick={onSaveSettings} disabled={saveSettingsMutation.isPending || !dirty}>
                {saveSettingsMutation.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
                Save Advanced Settings
              </Button>
              <Button variant="secondary" onClick={() => syncRulebookMutation.mutate()} disabled={syncRulebookMutation.isPending}>
                {syncRulebookMutation.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                Sync Rulebook Now
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Search className="size-4 text-sky-600" />
              Retrieval + Memory
            </CardTitle>
            <CardDescription>Confirm your game is actually using PDFs and campaign memory.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
              <Label>Knowledge Search</Label>
              <div className="flex gap-2">
                <Input value={kbQuery} onChange={(e) => setKbQuery(e.target.value)} placeholder="Ask a rules question..." />
                <Button
                  variant="secondary"
                  disabled={!kbQuery.trim() || kbSearchMutation.isPending}
                  onClick={() => kbSearchMutation.mutate(kbQuery)}
                >
                  {kbSearchMutation.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Search className="size-4" />}
                  Run
                </Button>
              </div>
              <div className="max-h-48 space-y-2 overflow-y-auto">
                {kbResults.length === 0 ? (
                  <div className="text-xs text-slate-500">No search results yet.</div>
                ) : (
                  kbResults.map((row, idx) => (
                    <div key={`${idx}_${String(row.score || 0)}`} className="rounded-lg border border-slate-200 bg-white p-2 text-xs">
                      <div className="mb-1 font-semibold text-slate-700">score: {(row.score || 0).toFixed?.(3) || row.score}</div>
                      <div className="line-clamp-4 text-slate-600">{String(row.text || "")}</div>
                    </div>
                  ))
                )}
              </div>
            </div>

            <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
              <div className="flex items-center justify-between">
                <Label className="mb-0">Memory</Label>
                <Select value={memoryScope} onChange={(e) => setMemoryScope(e.target.value as typeof memoryScope)} className="w-40">
                  <option value="campaign">Campaign</option>
                  <option value="session">Session</option>
                  <option value="player">Player</option>
                </Select>
              </div>
              <div className="max-h-56 space-y-2 overflow-y-auto">
                {memoryQuery.isLoading ? (
                  <div className="text-xs text-slate-500">Loading memory...</div>
                ) : (memoryQuery.data || []).length === 0 ? (
                  <div className="text-xs text-slate-500">No memory rows.</div>
                ) : (
                  (memoryQuery.data || []).map((entry: MemoryEntry, idx: number) => (
                    <div key={`${idx}_${entry.ts || ""}`} className="rounded-lg border border-slate-200 bg-white p-2 text-xs">
                      <div className="mb-1 text-slate-500">{entry.ts || "timestamp unknown"}</div>
                      <div className="text-slate-700">
                        <span className="font-semibold">{entry.player_id || "player"}:</span> {entry.player_text || ""}
                      </div>
                      <div className="text-slate-700">
                        <span className="font-semibold">GM:</span> {entry.gm_text || ""}
                      </div>
                    </div>
                  ))
                )}
              </div>
              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => memoryQuery.refetch()}>
                  Refresh
                </Button>
                <Button variant="danger" onClick={() => clearMemoryMutation.mutate()} disabled={clearMemoryMutation.isPending}>
                  Clear Memory
                </Button>
              </div>
            </div>

            <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
              <Label>Campaign Controls</Label>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={() => campaignNewMutation.mutate()} disabled={campaignNewMutation.isPending}>
                  New Campaign
                </Button>
                <Button variant="secondary" onClick={() => campaignResumeMutation.mutate()} disabled={campaignResumeMutation.isPending}>
                  Resume Latest
                </Button>
                <Button variant="danger" onClick={() => campaignResetMutation.mutate()} disabled={campaignResetMutation.isPending}>
                  Reset Campaign
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  };

  return (
    <main className="relative min-h-screen overflow-x-hidden px-4 py-6 md:px-8 md:py-8">
      <div className="pointer-events-none absolute -top-24 left-1/2 h-80 w-[44rem] -translate-x-1/2 rounded-full bg-[radial-gradient(circle_at_center,rgba(20,184,166,0.22),rgba(15,98,254,0.10),transparent_72%)]" />
      <div className="pointer-events-none absolute -bottom-24 right-0 h-64 w-64 rounded-full bg-[radial-gradient(circle_at_center,rgba(14,165,233,0.24),transparent_70%)]" />

      <div className="mx-auto max-w-[1280px] space-y-4">
        <motion.section
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35 }}
          className="rounded-3xl border border-slate-200/90 bg-white/75 p-5 shadow-[0_18px_45px_rgba(15,23,42,0.10)] backdrop-blur"
        >
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight text-slate-900 md:text-3xl">Game Master Studio 2.0</h1>
              <p className="mt-1 max-w-3xl text-sm text-slate-600">
                Premium control surface for tabletop campaigns: setup, rulebook ingestion, diarized players, live GM turns, and persistent campaign memory.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge className={cn(statusQuery.data?.openai_api_key_present ? "border-emerald-300 text-emerald-700" : "border-amber-300 text-amber-700")}>
                <Bot className="mr-1 size-3" /> OpenAI {statusQuery.data?.openai_api_key_present ? "ready" : "missing"}
              </Badge>
              <Badge className={cn(statusQuery.data?.deepgram_api_key_present ? "border-emerald-300 text-emerald-700" : "")}>Deepgram</Badge>
              <Badge className={cn(statusQuery.data?.elevenlabs_api_key_present ? "border-emerald-300 text-emerald-700" : "")}>ElevenLabs</Badge>
              <Badge className={cn(statusQuery.data?.codex_chatgpt_available ? "border-emerald-300 text-emerald-700" : "")}>
                ChatGPT Auth {statusQuery.data?.codex_chatgpt_available ? "ready" : "off"}
              </Badge>
              <Badge className={cn(draft?.knowledge.enabled ? "border-emerald-300 text-emerald-700" : "")}>
                <Server className="mr-1 size-3" /> Knowledge {draft?.knowledge.enabled ? "enabled" : "off"}
              </Badge>
            </div>
          </div>
        </motion.section>

        <section className="flex flex-wrap gap-2">
          {(Object.keys(tabLabels) as Array<keyof typeof tabLabels>).map((tab) => (
            <Button
              key={tab}
              variant={activeTab === tab ? "default" : "secondary"}
              onClick={() => setActiveTab(tab)}
              className={cn("min-w-28", activeTab === tab && "shadow-[0_8px_22px_rgba(15,98,254,0.25)]")}
            >
              {tabLabels[tab]}
            </Button>
          ))}
        </section>

        <AnimatePresence mode="wait">
          <motion.section
            key={activeTab}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.22 }}
          >
            {renderTab()}
          </motion.section>
        </AnimatePresence>

        <section className="grid gap-3 rounded-2xl border border-slate-200 bg-white/70 p-4 text-xs text-slate-600 md:grid-cols-3">
          <div className="flex items-start gap-2">
            <Globe className="mt-0.5 size-4 text-sky-600" />
            <div>
              <div className="font-semibold text-slate-800">Language routing</div>
              GM responses follow speaker language when <code>response_language_mode=player</code>.
            </div>
          </div>
          <div className="flex items-start gap-2">
            <Brain className="mt-0.5 size-4 text-sky-600" />
            <div>
              <div className="font-semibold text-slate-800">Rulebook + Qdrant</div>
              Retrieval uses active docs only. Use Advanced tab search to verify citations and scoring.
            </div>
          </div>
          <div className="flex items-start gap-2">
            <CheckCircle2 className="mt-0.5 size-4 text-sky-600" />
            <div>
              <div className="font-semibold text-slate-800">Save without connect</div>
              Settings, API keys, uploads, and memory controls all work via HTTP APIs, no websocket connect required.
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
