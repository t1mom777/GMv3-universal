# Production Deployment (Coolify / Docker Compose)

This app is designed to run as a 3-service stack:

1. `gm-app` (Python voice runtime, serves UI at `/`)
2. `qdrant` (vector database)
3. `nginx` (single-domain reverse proxy for `/` + `/ws`)

## Files

- `Dockerfile`
- `.dockerignore`
- `docker-compose.coolify.yml`
- `nginx.conf`

## 1) Required Environment Variables

Set these in Coolify (or `.env` for local compose):

- `OPENAI_API_KEY` (required)
- `OPENAI_BASE_URL` (optional)
- `GM_LLM_MODEL` (optional, default `gpt-4.1-mini`)
- `GM_STT_MODEL` (optional, default `gpt-4o-transcribe`)
- `GM_TTS_MODEL` (optional, default `gpt-4o-mini-tts`)
- `GM_TTS_VOICE` (optional, default `alloy`)
- `GM_EMBEDDING_MODEL` (optional, default `text-embedding-3-small`)
- `GM_KB_BACKEND` (`remote` for compose deployment)
- `QDRANT_URL` (`http://qdrant:6333` inside the compose network)
- `GM_KB_SPLIT_COLLECTIONS` (`true` recommended)
- `GM_QDRANT_GAME_COLLECTION` (default `gm_knowledge_game`)
- `GM_QDRANT_GUIDANCE_COLLECTION` (default `gm_knowledge_guidance`)

## 2) Deploy

Use `docker-compose.coolify.yml` as the compose file.

Local verification:

```bash
docker compose -f docker-compose.coolify.yml up -d --build
curl -fsS http://localhost/health
```

Expected health payload includes `ok: true`.

## 3) Networking

Public entrypoint is Nginx:

- `https://your-domain/` -> UI (proxied to `gm-app:8000`)
- `wss://your-domain/ws` -> voice websocket (proxied to `gm-app:8765`)

The browser UI auto-uses `/ws` when opened on a non-localhost domain.

## 4) Persistence

- `gm_data` volume -> `/app/data` (settings, sqlite state, uploads, events)
- `qdrant_data` volume -> `/qdrant/storage`

Back up both volumes for disaster recovery.

## 5) Health and Logs

- App health endpoint: `GET /health`
- Compose health checks:
  - `gm-app` checks `http://127.0.0.1:8000/health`
  - `nginx` checks `http://127.0.0.1/health`

Useful log commands:

```bash
docker compose -f docker-compose.coolify.yml logs -f gm-app
docker compose -f docker-compose.coolify.yml logs -f nginx
docker compose -f docker-compose.coolify.yml logs -f qdrant
```

## 6) Rollback

1. Re-deploy previous Git commit (or previous image tag) in Coolify.
2. Keep existing `gm_data` and `qdrant_data` volumes.
3. Verify `/health` and websocket connect from browser UI.

## 7) Pre-release Regression Command

Run this before each production release:

```bash
./.venv-voice/bin/python scripts/regression_check.py
```

This validates compile, websocket control RPCs, and end-to-end STT/TTS voice flow.
