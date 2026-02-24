import type {
  AppSettings,
  ElevenLabsVoice,
  KBDocument,
  MemoryEntry,
  SearchResult,
  SecretsResponse,
  ServerStatus,
  SetupSystemHint,
} from "@/lib/types";

export type JsonObject = Record<string, unknown>;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });

  let payload: JsonObject = {};
  try {
    payload = (await res.json()) as JsonObject;
  } catch {
    payload = {};
  }

  if (!res.ok) {
    const err = typeof payload.error === "string" ? payload.error : `HTTP ${res.status}`;
    throw new Error(err);
  }
  return payload as T;
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

export async function getServerStatus() {
  return request<ServerStatus>("/api/server_status", { method: "GET" });
}

export async function getSettings() {
  const payload = await request<{ type: string; settings: AppSettings }>("/api/settings", { method: "GET" });
  return payload.settings;
}

export async function updateSettings(settings: AppSettings) {
  return request<{
    type: string;
    settings: AppSettings;
    restart_required?: boolean;
    restart_keys?: string[];
    rulebook_sync?: unknown;
  }>(
    "/api/settings",
    {
      method: "PATCH",
      body: JSON.stringify({ settings }),
    }
  );
}

export async function getSecrets() {
  return request<SecretsResponse>("/api/secrets", { method: "GET" });
}

export async function updateSecrets(payload: { updates?: Record<string, string>; clear_keys?: string[] }) {
  return request<SecretsResponse & { updated_keys?: string[]; cleared_keys?: string[] }>("/api/secrets", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getWsUrl() {
  const payload = await request<{ type: string; url: string }>("/api/ws_url", { method: "GET" });
  return payload.url;
}

export async function searchSystems(query: string, limit = 8) {
  const q = encodeURIComponent(query);
  const payload = await request<{ type: string; query: string; results: SetupSystemHint[] }>(
    `/api/setup_system_search?query=${q}&limit=${Math.max(3, Math.min(12, limit))}`,
    { method: "GET" }
  );
  return payload.results || [];
}

export async function getKbDocuments() {
  const payload = await request<{ type: string; documents: KBDocument[] }>("/api/kb/documents", { method: "GET" });
  return payload.documents || [];
}

export async function kbUploadStart(payload: {
  filename: string;
  doc_id?: string;
  ruleset?: string;
  doc_kind?: string;
  collection_target?: string;
  total_bytes?: number;
}) {
  return request<{ type: string; upload_id: string; doc_id: string }>("/api/kb/upload_start", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function kbUploadChunk(payload: { upload_id: string; seq: number; data_b64: string }) {
  return request<{ type: string; received_bytes: number; total_bytes?: number; seq: number }>(
    "/api/kb/upload_chunk",
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}

export async function kbUploadFinish(upload_id: string) {
  return request<{ type: string; doc_id: string; path?: string; received_bytes?: number }>("/api/kb/upload_finish", {
    method: "POST",
    body: JSON.stringify({ upload_id }),
  });
}

export async function uploadPdfInChunks(
  file: File,
  opts: {
    docId?: string;
    ruleset?: string;
    docKind?: string;
    collectionTarget?: string;
    chunkSize?: number;
    onProgress?: (doneBytes: number, totalBytes: number) => void;
  }
) {
  const start = await kbUploadStart({
    filename: file.name,
    doc_id: opts.docId,
    ruleset: opts.ruleset,
    doc_kind: opts.docKind,
    collection_target: opts.collectionTarget,
    total_bytes: file.size,
  });

  const buffer = new Uint8Array(await file.arrayBuffer());
  const chunkSize = opts.chunkSize || 48 * 1024;
  let seq = 0;
  for (let i = 0; i < buffer.length; i += chunkSize) {
    const chunk = buffer.subarray(i, i + chunkSize);
    await kbUploadChunk({
      upload_id: start.upload_id,
      seq,
      data_b64: bytesToBase64(chunk),
    });
    seq += 1;
    opts.onProgress?.(Math.min(i + chunk.length, buffer.length), buffer.length);
  }

  const finish = await kbUploadFinish(start.upload_id);
  return { ...finish, upload_id: start.upload_id, doc_id: start.doc_id };
}

export async function syncRulebook(payload?: { ingest?: boolean; activate?: boolean }) {
  return request<{ type: string; status: string; result?: JsonObject }>("/api/kb/sync_rulebook", {
    method: "POST",
    body: JSON.stringify(payload || {}),
  });
}

export async function ingestDoc(payload: {
  doc_id: string;
  replace_existing?: boolean;
  chunk_max_chars?: number;
  chunk_overlap?: number;
  ruleset?: string;
}) {
  return request<{ type: string; status: string; doc_id: string }>("/api/kb/ingest", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteDoc(payload: { doc_id: string; delete_file?: boolean }) {
  return request<{ type: string; doc_id: string }>("/api/kb/delete", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function kbSearch(payload: {
  query: string;
  top_k?: number;
  chunk_type?: string;
  doc_kind?: string;
  collection_target?: string;
}) {
  return request<{ type: string; query: string; results: SearchResult[] }>("/api/kb/search", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getMemory(params: { scope?: string; limit?: number; session_id?: string; player_id?: string }) {
  const qp = new URLSearchParams();
  if (params.scope) qp.set("scope", params.scope);
  if (params.limit) qp.set("limit", String(params.limit));
  if (params.session_id) qp.set("session_id", params.session_id);
  if (params.player_id) qp.set("player_id", params.player_id);
  const payload = await request<{ type: string; entries: MemoryEntry[] }>(`/api/memory?${qp.toString()}`, {
    method: "GET",
  });
  return payload.entries || [];
}

export async function clearMemory(payload: { scope?: string; session_id?: string; player_id?: string }) {
  return request<{ type: string; cleared: number }>("/api/memory/clear", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function newCampaign(name?: string) {
  return request<{ type: string; settings: AppSettings; campaign?: { id: string; name?: string } }>(
    "/api/campaign/new",
    {
      method: "POST",
      body: JSON.stringify({ name }),
    }
  );
}

export async function resumeLatestCampaign() {
  return request<{ type: string; settings: AppSettings; campaign?: { id: string } }>(
    "/api/campaign/resume_latest",
    {
      method: "POST",
      body: JSON.stringify({}),
    }
  );
}

export async function resetCampaign() {
  return request<{ type: string; campaign_id: string; cleared_memory_entries: number; cleared_delayed_events: number }>(
    "/api/campaign/reset",
    {
      method: "POST",
      body: JSON.stringify({}),
    }
  );
}

export async function getElevenLabsVoices() {
  const payload = await request<{ type: string; voices: ElevenLabsVoice[] }>("/api/elevenlabs/voices", {
    method: "GET",
  });
  return payload.voices || [];
}
