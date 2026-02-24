# Architecture Overview (Pipecat + Recursive Language Model)

## Mental model
- **Pipecat = microphone & speaker** (STT/TTS/turns)
- **RLM = brain** (bounded recursion, deterministic orchestration)
- **Knowledge base = library** (Qdrant vector store for PDFs)
- **World state = reality** (SQL tables for entities + facts)

This repo enforces a hard separation:

1. **Interaction Layer (Pipecat Adapter)**
   - Accepts streaming STT transcripts
   - Emits streaming TTS audio
   - Maintains session/turn ids
   - Calls **only** into the RLM Controller

2. **RLM Controller (Core Intelligence)**
   - Implements bounded recursive reasoning (depth 0..N)
   - Produces a *plan* of state reads/writes and optional knowledge retrieval
   - Decides whether/when to call an LLM (via an injected LLM provider)
   - Auditable: every step recorded as structured events

3. **Knowledge & Memory (External, Persistent)**
   - Qdrant collection: chunked PDF content with tags and metadata
   - Ingestion pipeline: parse → chunk → classify → embed → upsert
   - Retrieval is never “blind RAG”: RLM decides if/when to query

4. **Structured World State (SQL)**
   - Single source of truth: campaigns, players, characters, NPCs, locations, quests
   - Append-only event log for continuity and audits

## Required per-turn RLM loop
For every player turn:

1) Interpret player intent
2) Decide what knowledge is required
3) Inspect structured state and/or Qdrant
4) Resolve immediate consequences
5) Detect unresolved or delayed effects
6) Recurse if needed (max depth 2–3)
7) Commit state updates (transaction)
8) Narrate outcome (streaming)
9) Schedule delayed events asynchronously

Hard limits:
- `max_depth` default 2
- `max_llm_calls_per_turn` default 3
- Token/cost budgets enforced in controller

## Latency strategy (<1200ms narration start)
- The controller returns a **NarrationPlan** quickly:
  - immediate narration text (first chunk)
  - background tasks: embeddings/logging/delayed events
- TTS streaming begins as soon as the first narration chunk is available.
- All persistence and embedding writes are async and never block narration.

## Data flows

### Turn handling
STT stream → (Pipecat Adapter) → `RLMController.handle_turn()` →
- optional SQL reads
- optional Qdrant retrieval
- optional LLM calls (only inside controller)
→ SQL transaction commit
→ immediate narration text
→ streaming TTS
→ background: logs, delayed events, ingestion

### PDF ingestion
Upload → parse PDF → chunk (semantic + heuristic) → classify chunk type → embed → upsert to Qdrant

## Extensibility
- Multiple campaigns: keyed by `campaign_id`
- Multiple rulesets: chunk tags + campaign ruleset settings
- Multiple clients: Pipecat is just one adapter; future GUI client can call the same controller.

## Non-goals (for this iteration)
- Full Pipecat provider wiring (all transports/providers, advanced config, barge-in)
- Advanced game rules engine for every system (we provide a framework + knowledge queries)

## What’s implemented here
- A minimal Pipecat voice loop via websocket transport + browser client (`python gm.py`).
- RLM reasoning remains isolated in `gm_engine.rlm.controller.RLMController` (Pipecat never calls the LLM directly).
