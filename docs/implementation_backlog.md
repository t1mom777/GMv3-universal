# Trusted Implementation Backlog

Source used: `ChatExport_2026-02-20/messages.html`.

This backlog keeps only decisions that fit the current repo architecture and runtime behavior.

## Locked Decisions

1. Runtime entrypoint stays `python gm.py` (default voice websocket mode).
2. Voice stack stays Pipecat websocket + browser UI (`:8000` UI, `:8765` WS in local dev).
3. Python 3.13 is required for voice mode (`pipecat-ai` dependency constraint).
4. RLM orchestration remains in `gm_engine.rlm.controller.RLMController` only.
5. Knowledge flow remains upload -> ingest -> chunk/classify/embed -> retrieve (Qdrant local or remote).
6. All major configuration must be editable in GUI (settings, prompts, knowledge, memory).
7. Production target should be single-domain UX (`/` for UI, `/ws` for websocket) behind a reverse proxy.

## Instructions To Ignore From Chat Export

1. Any "source of truth" references to other repos (this repo is authoritative).
2. Any instructions touching unrelated external workspace files (`WORKFLOW_AUTO.md`, external memory folders).
3. Any plaintext secrets present in chat logs (must not be reused).
4. Any unverified Deepgram migration/schema rewrite steps that bypass current tested runtime.

## Security Actions (Immediate)

1. Rotate any API key exposed in messenger export or terminal history.
2. Keep `.env` untracked and never paste real keys into chat/exported docs.
3. Add a pre-commit secret scan before future production deploys.

## Priority Backlog

## P0 - Runtime Stability (must pass before new features)

1. Websocket client lifecycle hardening.
Success criteria:
- reconnecting from UI works repeatedly without stale single-client lockouts.
- no long-lived `CLOSE-WAIT` accumulation during normal use.

2. Voice path reliability in both modes.
Success criteria:
- push-to-talk and auto VAD both produce transcript + GM response + audio.
- failures return actionable UI errors (mic blocked, STT down, key missing).

3. Control RPC reliability.
Success criteria:
- `settings_get`, `settings_update`, `prompts_generate`, `prompt_generate`, `kb_*`, `memory_*` all return with `req_id`.
- no handler can block the pipeline indefinitely.

4. Regression checks in CI/local script.
Success criteria:
- one command validates compile + websocket smoke + core RPCs.

## P1 - Production Packaging

1. Add production compose stack in repo (`gm-app`, `qdrant`, reverse proxy).
Success criteria:
- single domain serves UI and websocket.
- persistent volumes mounted for `/data` and qdrant storage.

2. Add deployment runbook (`DEPLOY.md`) aligned to current repo.
Success criteria:
- includes env var matrix, health checks, rollback, log locations.

3. Add health probes.
Success criteria:
- container health reflects UI and websocket readiness.

## P2 - Product Features (User Requested)

1. Rulebook intelligence improvements.
Scope:
- better chunk typing and structure extraction for lore, characters, missions, locations, factions, items, monsters, story beats, and rules.
Success criteria:
- ingest report shows type distribution.
- retrieval can filter by type/doc/ruleset in UI.

2. Split knowledge collections by purpose.
Scope:
- game-specific materials vs general GM best-practices.
Success criteria:
- user can upload/select each collection separately.
- retrieval routing can include one or both collections.

3. Model management UX.
Scope:
- dropdown presets plus custom model entries persisted in settings.
Success criteria:
- non-technical users can change LLM/STT/TTS/embedding without editing files.

4. Prompt helper UX.
Scope:
- in-UI examples and explanations.
- prompt generation that works with and without knowledge enabled.
Success criteria:
- generated prompts are saved and testable immediately.

5. Session/player memory UX.
Scope:
- clear campaign/session/player switching and memory visibility.
Success criteria:
- resume latest campaign works and history hydration is predictable.

## Test Matrix (Definition of "Production Ready")

1. Startup: `python gm.py` in clean shell prints browser URL and binds successfully.
2. Browser: connect/disconnect/reconnect cycles without server restart.
3. Voice PTT: transcript, text response, audio response.
4. Voice VAD: transcript, text response, audio response.
5. Prompt generation: field-level and all-prompts generation return and persist.
6. Knowledge upload + ingest + search works in local backend mode.
7. Memory list + clear + campaign resume works.
8. Failures show user-readable errors (missing API key, blocked mic, backend unavailable).

## Execution Order (Recommended)

1. Finish remaining P0 websocket lifecycle hardening.
2. Add automated regression command/script.
3. Implement P1 packaging and deployment docs.
4. Deliver P2 rulebook intelligence and knowledge collection split.
5. Polish model/prompt UX and finalize release checklist.

