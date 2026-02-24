from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib import parse as urlparse

from gm_engine.app.settings_store import SettingsStore
from gm_engine.interaction.pipecat_adapter import PipecatAdapter, StreamingTTS
from gm_engine.knowledge.manager import KnowledgeManager
from gm_engine.knowledge.null_store import NullKnowledgeStore
from gm_engine.llm.codex_provider import CodexChatGPTLLM
from gm_engine.logging.events import EventLogger
from gm_engine.llm.openai_provider import OpenAIChatLLM
from gm_engine.rlm.controller import RLMController
from gm_engine.rlm.types import RetrievalSpec, StateReadSpec, TurnContext
from gm_engine.state.store import WorldStateStore


class DummyLLM:
    async def complete(self, *, system: str, user: str, temperature: float = 0.2) -> str:
        # Replace with real provider (OpenAI, etc.) behind this interface.
        return "The scene advances. Tell me exactly what you do next."


class DummyStreamingTTS:
    async def synthesize_stream(self, text: str, *, voice: str, locale: str):
        # Replace with real streaming TTS.
        yield text.encode("utf-8")


async def run_text_mode(adapter: PipecatAdapter):
    print("Text mode. Type player input; Ctrl+C to exit.")
    i = 0
    while True:
        i += 1
        try:
            transcript = input("player> ").strip()
        except EOFError:
            break
        ctx = TurnContext(
            campaign_id="demo",
            session_id="local",
            turn_id=str(i),
            player_id="player1",
            transcript_text=transcript,
        )
        audio_chunks = []
        async for b in adapter.on_final_transcript(ctx):
            audio_chunks.append(b)
        print("gm>", b"".join(audio_chunks).decode("utf-8", errors="ignore"))


def _try_load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        root = Path(__file__).resolve().parents[1]
        # Do not clobber existing env vars (e.g. when running under a process manager).
        dotenv_path = root / ".env"
        load_dotenv(dotenv_path=dotenv_path, override=False)

        # openai-python treats OPENAI_BASE_URL as authoritative if present.
        # An empty value results in "Request URL is missing http/https" errors.
        base_url = os.environ.get("OPENAI_BASE_URL")
        if base_url is not None and not base_url.strip():
            os.environ.pop("OPENAI_BASE_URL", None)
            # If the variable was set but blank, allow .env to populate it.
            load_dotenv(dotenv_path=dotenv_path, override=False)
            base_url = os.environ.get("OPENAI_BASE_URL")
            if base_url is not None and not base_url.strip():
                os.environ.pop("OPENAI_BASE_URL", None)
    except ModuleNotFoundError:
        return


def _build_llm_provider(*, settings_store: SettingsStore) -> Any:
    s = settings_store.get()
    provider = str(getattr(s.openai, "llm_provider", "openai") or "openai").strip().lower()
    model = str(getattr(s.openai, "llm_model", "gpt-4o-mini") or "gpt-4o-mini").strip()

    has_openai_key = bool(str(os.environ.get("OPENAI_API_KEY") or "").strip())
    codex_ok, codex_status = CodexChatGPTLLM.login_status()

    if provider == "codex_chatgpt":
        if codex_ok:
            print(f"[llm] Using codex_chatgpt ({model}) [{codex_status}]")
            return CodexChatGPTLLM(model=model)
        if has_openai_key:
            print("[llm] codex_chatgpt unavailable; falling back to OpenAI API key provider.")
            return OpenAIChatLLM(model=model)
        print("[llm] No OpenAI API key and codex login is unavailable. Using dummy fallback model.")
        return DummyLLM()

    # provider == openai
    if has_openai_key:
        print(f"[llm] Using openai ({model})")
        return OpenAIChatLLM(model=model)
    if codex_ok:
        print("[llm] OPENAI_API_KEY is missing; falling back to codex_chatgpt session login.")
        return CodexChatGPTLLM(model=model)
    print("[llm] No OpenAI API key and no codex login session. Using dummy fallback model.")
    return DummyLLM()


_PORT_PID_RE = re.compile(r"\bpid=(\d+)\b")


def _is_truthy_env(name: str, default: str = "1") -> bool:
    v = str(os.environ.get(name, default) or "").strip().lower()
    return v not in {"", "0", "false", "no", "off"}


def _list_listening_pids_for_port(port: int) -> set[int]:
    pids: set[int] = set()
    if int(port) <= 0:
        return pids

    # Prefer ss (Linux), then lsof, then fuser.
    try:
        out = subprocess.run(
            ["ss", "-ltnpH", f"sport = :{int(port)}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if out.stdout:
            for m in _PORT_PID_RE.finditer(out.stdout):
                try:
                    pids.add(int(m.group(1)))
                except Exception:
                    pass
    except Exception:
        pass

    if pids:
        return pids

    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"],
            check=False,
            capture_output=True,
            text=True,
        )
        if out.stdout:
            for ln in out.stdout.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    pids.add(int(ln))
                except Exception:
                    pass
    except Exception:
        pass

    if pids:
        return pids

    try:
        out = subprocess.run(
            ["fuser", "-n", "tcp", str(int(port))],
            check=False,
            capture_output=True,
            text=True,
        )
        text = " ".join([out.stdout or "", out.stderr or ""])
        for token in re.findall(r"\b\d+\b", text):
            try:
                pids.add(int(token))
            except Exception:
                pass
    except Exception:
        pass

    return pids


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _terminate_pid(pid: int, *, term_timeout_secs: float = 1.5) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except Exception:
        return False

    deadline = time.monotonic() + term_timeout_secs
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.05)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except Exception:
        return False

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.05)
    return not _pid_exists(pid)


def _free_port_by_terminating_listeners(port: int, *, label: str, protected_pids: set[int]) -> list[int]:
    if int(port) <= 0:
        return []
    pids = _list_listening_pids_for_port(int(port))
    victims = sorted(pid for pid in pids if pid > 1 and pid not in protected_pids)
    killed: list[int] = []
    if victims:
        print(f"[startup] {label} port {int(port)} is busy; terminating listeners: {victims}", flush=True)
    for pid in victims:
        if _terminate_pid(pid):
            killed.append(pid)
    # A process can linger briefly as a zombie even after releasing the socket.
    # Decide success by whether it still owns a listening socket on this port.
    remaining = _list_listening_pids_for_port(int(port))
    for pid in victims:
        if pid not in remaining and pid not in killed:
            killed.append(pid)
    if victims and killed:
        print(f"[startup] Freed {label} port {int(port)} by terminating: {killed}", flush=True)
    if victims and len(killed) != len(victims):
        failed = [pid for pid in victims if pid in remaining]
        print(
            f"[startup] Warning: could not terminate all listeners on {label} port {int(port)}: {failed}. "
            "Startup may still fail with address already in use.",
            flush=True,
        )
    return killed


def _make_http_server(
    directory: Path,
    host: str,
    port: int,
    *,
    health_payload: Callable[[], dict[str, Any]] | None = None,
    settings_store: SettingsStore | None = None,
    controller: RLMController | None = None,
    knowledge_manager: KnowledgeManager | None = None,
    state: WorldStateStore | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
):
    import http.server
    import socketserver

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def _run_coro(self, coro):
            if loop is None:
                return asyncio.run(coro)
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            return fut.result()

        def _ctx_from_settings(self, settings: Any) -> TurnContext:
            return TurnContext(
                campaign_id=str(settings.voice.campaign_id),
                session_id=str(settings.voice.session_id),
                turn_id="0",
                player_id=str(settings.voice.player_id),
                transcript_text="",
                locale=str(settings.voice.locale),
            )

        def do_GET(self):
            split = urlparse.urlsplit(self.path)
            path = split.path or "/"
            query = urlparse.parse_qs(split.query)

            if path == "/api/ws_url":
                if settings_store is None:
                    self._send_json(503, {"type": "error", "error": "settings store unavailable"})
                    return
                settings = settings_store.get()
                ws_host = str(settings.voice.ws_host or "").strip() or "localhost"
                ws_port = int(settings.voice.ws_port)
                req_host_raw = str(self.headers.get("Host") or "").strip()
                req_host = ""
                if req_host_raw:
                    if req_host_raw.startswith("["):
                        end = req_host_raw.find("]")
                        req_host = req_host_raw[1:end] if end > 1 else ""
                    else:
                        req_host = req_host_raw.split(":")[0].strip()
                if ws_host in {"0.0.0.0", "::"}:
                    ws_host = req_host or "localhost"
                scheme = "ws"
                xfp = str(self.headers.get("X-Forwarded-Proto") or "").strip().lower()
                if xfp == "https":
                    scheme = "wss"
                self._send_json(200, {"type": "ws_url", "url": f"{scheme}://{ws_host}:{ws_port}"})
                return

            if path in ("/health", "/readyz", "/healthz"):
                payload = (
                    health_payload() if callable(health_payload) else {"ok": True, "service": "voice-gm-ui"}
                )
                body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/api/server_status":
                if settings_store is None:
                    self._send_json(503, {"type": "error", "error": "settings store unavailable"})
                    return
                settings = settings_store.get()
                codex_ok, codex_status = CodexChatGPTLLM.login_status()
                self._send_json(
                    200,
                    {
                        "type": "server_status",
                        "openai_api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
                        "deepgram_api_key_present": bool(os.environ.get("DEEPGRAM_API_KEY")),
                        "elevenlabs_api_key_present": bool(os.environ.get("ELEVENLABS_API_KEY")),
                        "codex_chatgpt_available": bool(codex_ok),
                        "codex_chatgpt_status": codex_status,
                        "openai_base_url": (os.environ.get("OPENAI_BASE_URL") or ""),
                        "settings": settings.model_dump(),
                    },
                )
                return

            if path == "/api/settings":
                if settings_store is None:
                    self._send_json(503, {"type": "error", "error": "settings store unavailable"})
                    return
                self._send_json(200, {"type": "settings", "settings": settings_store.get().model_dump()})
                return

            if path == "/api/kb/documents":
                if knowledge_manager is None:
                    self._send_json(503, {"type": "error", "error": "knowledge manager unavailable"})
                    return
                try:
                    docs = self._run_coro(knowledge_manager.list_documents())
                    self._send_json(200, {"type": "kb_list", "documents": docs})
                except Exception as e:
                    self._send_json(500, {"type": "error", "error": f"kb_list failed: {e}"})
                return

            if path == "/api/memory":
                if settings_store is None or state is None:
                    self._send_json(503, {"type": "error", "error": "memory store unavailable"})
                    return
                try:
                    settings = settings_store.get()
                    scope = str((query.get("scope") or ["campaign"])[0] or "campaign").strip().lower()
                    if scope not in {"campaign", "session", "player"}:
                        scope = "campaign"
                    try:
                        limit = int((query.get("limit") or [50])[0] or 50)
                    except Exception:
                        limit = 50
                    limit = max(1, min(500, limit))
                    target_session = str((query.get("session_id") or [settings.voice.session_id])[0] or "").strip()
                    target_player = str((query.get("player_id") or [settings.voice.player_id])[0] or "").strip()
                    ctx = self._ctx_from_settings(settings)
                    params: dict[str, Any] = {"limit": limit}
                    if scope == "session" and target_session:
                        params["session_id"] = target_session
                    if scope == "player" and target_player:
                        params["player_id"] = target_player
                    view = self._run_coro(state.read(ctx, [StateReadSpec(kind="interaction_log", params=params)]))
                    self._send_json(
                        200,
                        {
                            "type": "memory",
                            "campaign_id": ctx.campaign_id,
                            "scope": scope,
                            "session_id": target_session,
                            "player_id": target_player,
                            "entries": list(view.get("interaction_log") or []),
                        },
                    )
                except Exception as e:
                    self._send_json(400, {"type": "error", "error": str(e)})
                return

            if path == "/api/secrets":
                try:
                    from gm_engine.interaction.control_processor import _secrets_payload

                    self._send_json(200, {"type": "secrets", "secrets": _secrets_payload()})
                except Exception as e:
                    self._send_json(500, {"type": "error", "error": f"secrets_get failed: {e}"})
                return

            if path == "/api/setup_system_search":
                try:
                    q = str((query.get("query") or [""])[0] or "").strip()
                    try:
                        limit = int((query.get("limit") or [8])[0])
                    except Exception:
                        limit = 8
                    limit = max(3, min(12, limit))

                    from gm_engine.interaction.control_processor import (
                        _duckduckgo_game_system_search,
                        _game_system_relevance_score,
                        _local_game_system_search,
                        _slug,
                        _wiki_game_system_search,
                    )

                    local = _local_game_system_search(q, limit=limit)
                    try:
                        wiki = _wiki_game_system_search(q, limit=limit)
                    except Exception:
                        wiki = []
                    try:
                        ddg = _duckduckgo_game_system_search(q, limit=limit)
                    except Exception:
                        ddg = []

                    merged_scored: list[tuple[float, dict[str, Any]]] = []
                    seen: set[str] = set()
                    for item in [*local, *wiki, *ddg]:
                        name_key = _slug(str(item.get("name") or ""), fallback="")
                        if not name_key or name_key in seen:
                            continue
                        seen.add(name_key)
                        score = _game_system_relevance_score(item, q)
                        it = dict(item)
                        it["score"] = round(score, 3)
                        merged_scored.append((score, it))
                    merged_scored.sort(
                        key=lambda x: (
                            -x[0],
                            0 if str((x[1] or {}).get("source") or "") == "preset" else 1,
                            str((x[1] or {}).get("name") or "").lower(),
                        )
                    )
                    results = [item for _score, item in merged_scored[:limit]]
                    self._send_json(
                        200,
                        {"type": "setup_system_search_results", "query": q, "results": results},
                    )
                except Exception as e:
                    self._send_json(500, {"type": "error", "error": f"setup_system_search failed: {e}"})
                return

            if path == "/api/elevenlabs/voices":
                try:
                    key = str(os.environ.get("ELEVENLABS_API_KEY") or "").strip()
                    if not key:
                        raise RuntimeError("Missing ELEVENLABS_API_KEY.")
                    voices = _fetch_elevenlabs_voices(api_key=key)
                    self._send_json(200, {"type": "elevenlabs_voices", "voices": voices})
                except Exception as e:
                    self._send_json(400, {"type": "error", "error": str(e)})
                return

            return super().do_GET()

        def do_POST(self):
            split = urlparse.urlsplit(self.path)
            path = split.path or "/"

            if path == "/api/settings":
                self._handle_settings_update()
                return

            if path == "/api/secrets":
                self._handle_secrets_update()
                return

            if path == "/api/kb/upload_start":
                self._handle_kb_upload_start()
                return

            if path == "/api/kb/upload_chunk":
                self._handle_kb_upload_chunk()
                return

            if path == "/api/kb/upload_finish":
                self._handle_kb_upload_finish()
                return

            if path == "/api/kb/ingest":
                self._handle_kb_ingest()
                return

            if path == "/api/kb/sync_rulebook":
                self._handle_kb_sync_rulebook()
                return

            if path == "/api/kb/delete":
                self._handle_kb_delete()
                return

            if path == "/api/kb/search":
                self._handle_kb_search()
                return

            if path == "/api/memory/clear":
                self._handle_memory_clear()
                return

            if path == "/api/campaign/new":
                self._handle_campaign_new()
                return

            if path == "/api/campaign/resume_latest":
                self._handle_campaign_resume_latest()
                return

            if path == "/api/campaign/reset":
                self._handle_campaign_reset()
                return

            self._send_json(404, {"type": "error", "error": "not found"})

        def do_PATCH(self):
            split = urlparse.urlsplit(self.path)
            path = split.path or "/"
            if path == "/api/settings":
                self._handle_settings_update()
                return
            self._send_json(404, {"type": "error", "error": "not found"})

        def _read_json(self) -> dict[str, Any]:
            try:
                n = int(self.headers.get("Content-Length") or "0")
            except Exception:
                n = 0
            raw = self.rfile.read(n) if n > 0 else b"{}"
            try:
                obj = json.loads(raw.decode("utf-8", errors="ignore"))
            except Exception:
                raise RuntimeError("Invalid JSON payload")
            if not isinstance(obj, dict):
                raise RuntimeError("JSON object expected")
            return obj

        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            self.send_response(int(code))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _restart_keys(self, before: Any, after: Any) -> list[str]:
            keys: list[str] = []
            if before.voice.ws_host != after.voice.ws_host or before.voice.ws_port != after.voice.ws_port:
                keys.append("voice.ws_host/ws_port")
            if before.voice.http_host != after.voice.http_host or before.voice.http_port != after.voice.http_port:
                keys.append("voice.http_host/http_port")
            if (
                before.openai.stt_model != after.openai.stt_model
                or before.openai.stt_provider != after.openai.stt_provider
                or before.openai.deepgram_feature_profile != after.openai.deepgram_feature_profile
            ):
                keys.append("openai.stt_provider/stt_model/deepgram_feature_profile")
            if before.openai.llm_provider != after.openai.llm_provider:
                keys.append("openai.llm_provider")
            if (
                before.openai.tts_model != after.openai.tts_model
                or before.openai.tts_voice != after.openai.tts_voice
                or before.openai.tts_provider != after.openai.tts_provider
            ):
                keys.append("openai.tts_provider/tts_model/tts_voice")
            return keys

        def _handle_settings_update(self) -> None:
            if settings_store is None:
                self._send_json(503, {"type": "error", "error": "settings store unavailable"})
                return
            try:
                obj = self._read_json()
                patch = obj.get("patch")
                if patch is None and isinstance(obj.get("settings"), dict):
                    patch = obj.get("settings")
                if not isinstance(patch, dict):
                    raise RuntimeError("settings update requires JSON body with {patch:{...}}")

                before = settings_store.get()
                after = settings_store.update(patch)

                # Best-effort hot-apply for LLM model for next turns.
                if controller is not None and getattr(controller.llm, "model", None) is not None:
                    try:
                        controller.llm.model = after.openai.llm_model  # type: ignore[attr-defined]
                    except Exception:
                        pass

                rulebook_sync: dict[str, Any] | None = None
                if knowledge_manager is not None:
                    try:
                        rulebook_changed = _rulebook_sync_signature(before) != _rulebook_sync_signature(after)
                        source = str(after.knowledge.primary_rulebook_source or "path").strip().lower()
                        if source == "doc":
                            has_rulebook_source = bool(
                                str(
                                    after.knowledge.primary_rulebook_doc_choice or after.knowledge.primary_rulebook_doc_id or ""
                                ).strip()
                            )
                        else:
                            has_rulebook_source = bool(str(after.knowledge.primary_rulebook_path or "").strip())
                        should_auto_sync = has_rulebook_source and (
                            bool(after.knowledge.primary_rulebook_auto_ingest)
                            or bool(after.knowledge.primary_rulebook_auto_activate)
                        )
                        if rulebook_changed and should_auto_sync:
                            res = self._run_coro(knowledge_manager.sync_primary_rulebook())
                            rulebook_sync = {"ok": True, "result": res}
                    except Exception as e:
                        rulebook_sync = {"ok": False, "error": str(e)}

                restart_keys = self._restart_keys(before, after)
                self._send_json(
                    200,
                    {
                        "type": "settings",
                        "settings": after.model_dump(),
                        "restart_required": bool(restart_keys),
                        "restart_keys": restart_keys,
                        "rulebook_sync": rulebook_sync,
                    },
                )
            except Exception as e:
                self._send_json(400, {"type": "error", "error": str(e)})

        def _handle_secrets_update(self) -> None:
            try:
                obj = self._read_json()
                updates_raw = obj.get("updates")
                clear_raw = obj.get("clear_keys")
                if updates_raw is not None and not isinstance(updates_raw, dict):
                    raise RuntimeError("secrets_update: updates must be an object.")
                if clear_raw is not None and not isinstance(clear_raw, list):
                    raise RuntimeError("secrets_update: clear_keys must be a list.")

                from gm_engine.interaction.control_processor import (
                    _ENV_SECRET_FIELDS,
                    _apply_env_updates,
                    _secrets_payload,
                )

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

                _apply_env_updates(updates)

                self._send_json(
                    200,
                    {
                        "type": "secrets",
                        "secrets": _secrets_payload(),
                        "updated_keys": [k for k, v in updates.items() if v is not None],
                        "cleared_keys": [k for k, v in updates.items() if v is None],
                        "restart_required": True,
                        "restart_keys": ["openai.stt/tts/llm", "deepgram.stt", "elevenlabs.tts"],
                    },
                )
            except Exception as e:
                self._send_json(400, {"type": "error", "error": str(e)})

        def _handle_kb_upload_start(self) -> None:
            if knowledge_manager is None:
                self._send_json(503, {"type": "error", "error": "knowledge manager unavailable"})
                return
            try:
                obj = self._read_json()
                filename = str(obj.get("filename") or "upload.pdf")
                doc_id = str(obj.get("doc_id") or "").strip() or uuid.uuid4().hex
                ruleset = obj.get("ruleset")
                ruleset = str(ruleset).strip() if ruleset is not None and str(ruleset).strip() else None
                doc_kind = obj.get("doc_kind")
                doc_kind = str(doc_kind).strip() if doc_kind is not None and str(doc_kind).strip() else None
                collection_target = obj.get("collection_target")
                collection_target = (
                    str(collection_target).strip()
                    if collection_target is not None and str(collection_target).strip()
                    else None
                )
                total_bytes = obj.get("total_bytes")
                total_bytes_i = int(total_bytes) if total_bytes is not None else None
                upload_id, final_doc_id = self._run_coro(
                    knowledge_manager.begin_upload(
                        filename=filename,
                        doc_id=doc_id,
                        ruleset=ruleset,
                        doc_kind=doc_kind,
                        collection_target=collection_target,
                        total_bytes=total_bytes_i,
                    )
                )
                self._send_json(
                    200,
                    {"type": "kb_upload_started", "upload_id": upload_id, "doc_id": final_doc_id},
                )
            except Exception as e:
                self._send_json(400, {"type": "error", "error": str(e)})

        def _handle_kb_upload_chunk(self) -> None:
            if knowledge_manager is None:
                self._send_json(503, {"type": "error", "error": "knowledge manager unavailable"})
                return
            try:
                obj = self._read_json()
                upload_id = str(obj.get("upload_id") or "")
                seq = int(obj.get("seq") or 0)
                data_b64 = str(obj.get("data_b64") or "")
                res = self._run_coro(
                    knowledge_manager.upload_chunk(upload_id=upload_id, seq=seq, data_b64=data_b64)
                )
                self._send_json(200, {"type": "kb_upload_chunk_ack", **res})
            except Exception as e:
                self._send_json(400, {"type": "error", "error": str(e)})

        def _handle_kb_upload_finish(self) -> None:
            if knowledge_manager is None:
                self._send_json(503, {"type": "error", "error": "knowledge manager unavailable"})
                return
            try:
                obj = self._read_json()
                upload_id = str(obj.get("upload_id") or "")
                res = self._run_coro(knowledge_manager.finish_upload(upload_id=upload_id))
                self._send_json(200, {"type": "kb_upload_finished", **res})
            except Exception as e:
                self._send_json(400, {"type": "error", "error": str(e)})

        def _handle_kb_ingest(self) -> None:
            if knowledge_manager is None:
                self._send_json(503, {"type": "error", "error": "knowledge manager unavailable"})
                return
            try:
                obj = self._read_json()
                doc_id = str(obj.get("doc_id") or "").strip()
                if not doc_id:
                    raise RuntimeError("kb_ingest requires doc_id")
                replace_existing = bool(obj.get("replace_existing", True))
                chunk_max_chars = obj.get("chunk_max_chars")
                chunk_overlap = obj.get("chunk_overlap")
                ruleset = obj.get("ruleset")
                ruleset = str(ruleset).strip() if ruleset is not None and str(ruleset).strip() else None
                self._run_coro(
                    knowledge_manager.ingest_doc(
                        doc_id=doc_id,
                        chunk_max_chars=int(chunk_max_chars) if chunk_max_chars is not None else None,
                        chunk_overlap=int(chunk_overlap) if chunk_overlap is not None else None,
                        ruleset=ruleset,
                        replace_existing=replace_existing,
                    )
                )
                docs = self._run_coro(knowledge_manager.list_documents())
                latest = next((d for d in docs if str(d.get("doc_id") or "") == doc_id), None)
                self._send_json(200, {"type": "kb_ingest_status", "doc_id": doc_id, "status": "ready", "doc": latest})
            except Exception as e:
                self._send_json(400, {"type": "error", "error": str(e)})

        def _handle_kb_sync_rulebook(self) -> None:
            if knowledge_manager is None:
                self._send_json(503, {"type": "error", "error": "knowledge manager unavailable"})
                return
            try:
                obj = self._read_json()
                ingest = _optional_bool(obj.get("ingest"))
                activate = _optional_bool(obj.get("activate"))
                result = self._run_coro(
                    knowledge_manager.sync_primary_rulebook(ingest=ingest, activate=activate)
                )
                self._send_json(200, {"type": "kb_rulebook_sync_status", "status": "ready", "result": result})
            except Exception as e:
                self._send_json(400, {"type": "error", "error": str(e)})

        def _handle_kb_delete(self) -> None:
            if knowledge_manager is None:
                self._send_json(503, {"type": "error", "error": "knowledge manager unavailable"})
                return
            try:
                obj = self._read_json()
                doc_id = str(obj.get("doc_id") or "").strip()
                if not doc_id:
                    raise RuntimeError("kb_delete requires doc_id")
                delete_file = bool(obj.get("delete_file", False))
                self._run_coro(knowledge_manager.delete_doc(doc_id=doc_id, delete_file=delete_file))
                self._send_json(200, {"type": "kb_deleted", "doc_id": doc_id})
            except Exception as e:
                self._send_json(400, {"type": "error", "error": str(e)})

        def _handle_kb_search(self) -> None:
            if knowledge_manager is None or settings_store is None:
                self._send_json(503, {"type": "error", "error": "knowledge search unavailable"})
                return
            try:
                obj = self._read_json()
                q = str(obj.get("query") or "").strip()
                if not q:
                    raise RuntimeError("kb_search requires query")
                settings = settings_store.get()
                top_k = int(obj.get("top_k") or settings.knowledge.top_k or 5)
                top_k = max(1, min(12, top_k))
                chunk_type = str(obj.get("chunk_type") or "").strip()
                doc_kind = str(obj.get("doc_kind") or "").strip()
                collection_target = str(obj.get("collection_target") or "").strip()
                filters: dict[str, Any] = {}
                if chunk_type and chunk_type != "any":
                    filters["type"] = chunk_type
                if doc_kind and doc_kind != "any":
                    filters["doc_kind"] = doc_kind
                if collection_target and collection_target != "any":
                    filters["collection_target"] = collection_target
                if settings.knowledge.active_doc_ids:
                    filters["doc_id"] = list(settings.knowledge.active_doc_ids)
                ctx = self._ctx_from_settings(settings)
                res = self._run_coro(
                    knowledge_manager.search(
                        ctx,
                        RetrievalSpec(query=q, top_k=top_k, filters=filters or None),
                    )
                )
                self._send_json(200, {"type": "kb_search_results", "query": q, "results": res})
            except Exception as e:
                self._send_json(400, {"type": "error", "error": str(e)})

        def _handle_memory_clear(self) -> None:
            if settings_store is None or state is None:
                self._send_json(503, {"type": "error", "error": "memory store unavailable"})
                return
            try:
                obj = self._read_json()
                settings = settings_store.get()
                scope = str(obj.get("scope") or "campaign").strip().lower()
                if scope not in {"campaign", "session", "player"}:
                    scope = "campaign"
                target_session = str(obj.get("session_id") or settings.voice.session_id).strip()
                target_player = str(obj.get("player_id") or settings.voice.player_id).strip()
                ctx = self._ctx_from_settings(settings)
                if scope == "campaign":
                    cleared = self._run_coro(state.clear_interaction_log(ctx))
                elif scope == "session":
                    cleared = self._run_coro(
                        state.clear_interaction_log_filtered(ctx, session_id=target_session)
                    )
                else:
                    cleared = self._run_coro(
                        state.clear_interaction_log_filtered(ctx, player_id=target_player)
                    )
                self._send_json(
                    200,
                    {
                        "type": "memory_cleared",
                        "campaign_id": ctx.campaign_id,
                        "scope": scope,
                        "session_id": target_session,
                        "player_id": target_player,
                        "cleared": int(cleared),
                    },
                )
            except Exception as e:
                self._send_json(400, {"type": "error", "error": str(e)})

        def _handle_campaign_new(self) -> None:
            if settings_store is None:
                self._send_json(503, {"type": "error", "error": "settings store unavailable"})
                return
            try:
                obj = self._read_json()
                new_id = uuid.uuid4().hex
                name = str(obj.get("name") or "").strip() or None
                after = settings_store.update({"voice": {"campaign_id": new_id}})
                if state is not None:
                    ctx = self._ctx_from_settings(after)
                    self._run_coro(state.ensure_campaign(ctx, name=name))
                self._send_json(
                    200,
                    {
                        "type": "settings",
                        "settings": after.model_dump(),
                        "campaign": {"id": new_id, "name": name or f"Campaign {new_id}"},
                    },
                )
            except Exception as e:
                self._send_json(400, {"type": "error", "error": str(e)})

        def _handle_campaign_resume_latest(self) -> None:
            if settings_store is None or state is None:
                self._send_json(503, {"type": "error", "error": "campaign state unavailable"})
                return
            try:
                latest = self._run_coro(state.latest_campaign_id())
                if not latest:
                    raise RuntimeError("No saved campaigns found yet.")
                after = settings_store.update({"voice": {"campaign_id": latest}})
                self._send_json(
                    200,
                    {"type": "settings", "settings": after.model_dump(), "campaign": {"id": latest}},
                )
            except Exception as e:
                self._send_json(400, {"type": "error", "error": str(e)})

        def _handle_campaign_reset(self) -> None:
            if settings_store is None or state is None:
                self._send_json(503, {"type": "error", "error": "campaign state unavailable"})
                return
            try:
                settings = settings_store.get()
                ctx = self._ctx_from_settings(settings)
                cleared_log = self._run_coro(state.clear_interaction_log(ctx))
                cleared_events = self._run_coro(state.clear_delayed_events(ctx))
                self._send_json(
                    200,
                    {
                        "type": "campaign_reset",
                        "campaign_id": ctx.campaign_id,
                        "cleared_memory_entries": int(cleared_log),
                        "cleared_delayed_events": int(cleared_events),
                    },
                )
            except Exception as e:
                self._send_json(400, {"type": "error", "error": str(e)})

        def log_message(self, fmt: str, *args) -> None:  # quiet
            return

    class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    return ReusableThreadingTCPServer((host, port), Handler)


def _best_lan_ipv4() -> str | None:
    """Return a best-effort LAN IPv4 for printing clickable URLs."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return None


def _pretty_http_urls(host: str, port: int) -> list[str]:
    if port <= 0:
        return []
    if host in ("0.0.0.0", "::"):
        urls = [f"http://localhost:{port}/"]
        lan = _best_lan_ipv4()
        if lan:
            urls.append(f"http://{lan}:{port}/")
        return urls
    return [f"http://{host}:{port}/"]


def _pretty_ws_urls(host: str, port: int) -> list[str]:
    if port <= 0:
        return []
    if host in ("0.0.0.0", "::"):
        urls = [f"ws://localhost:{port}"]
        lan = _best_lan_ipv4()
        if lan:
            urls.append(f"ws://{lan}:{port}")
        return urls
    return [f"ws://{host}:{port}"]


def _should_sync_primary_rulebook(settings: Any) -> bool:
    k = settings.knowledge
    source = str(k.primary_rulebook_source or "path").strip().lower()
    if source == "doc":
        doc = str(k.primary_rulebook_doc_choice or k.primary_rulebook_doc_id or "").strip()
        if not doc:
            return False
    else:
        path = str(k.primary_rulebook_path or "").strip()
        if not path:
            return False
    return bool(k.primary_rulebook_auto_activate) or bool(k.primary_rulebook_auto_ingest)


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


def _normalize_locale_tag(value: str | None) -> str:
    s = str(value or "").strip().replace("_", "-")
    return s or "en-US"


def _resolve_pipecat_language_for_locale(locale: str | None, LanguageEnum: Any) -> Any:
    tag = _normalize_locale_tag(locale)
    attempts = [tag]
    if "-" in tag:
        attempts.append(tag.split("-", 1)[0])
    for cand in attempts:
        try:
            return LanguageEnum(cand)
        except Exception:
            pass
        attr = getattr(LanguageEnum, cand.replace("-", "_").upper(), None)
        if attr is not None:
            return attr
    return getattr(LanguageEnum, "EN", None)


def _resolve_deepgram_language(settings: Any) -> str:
    mode = str(getattr(settings.prompts, "response_language_mode", "") or "").strip().lower()
    if mode == "player":
        # Player-language mode should not pin STT to a single locale.
        return "multi"
    return _normalize_locale_tag(getattr(settings.voice, "locale", "en-US"))


def _resolve_deepgram_live_options(settings: Any) -> dict[str, Any]:
    profile = str(getattr(settings.openai, "deepgram_feature_profile", "") or "").strip().lower()
    if profile not in {
        "speaker_diarization",
        "multilingual",
        "auto_language_detection",
        "multilingual_diarization",
    }:
        profile = "speaker_diarization"

    cfg: dict[str, Any] = {
        "profile": profile,
        "model": "nova-3-general",
        "language": _resolve_deepgram_language(settings),
        "diarize": True,
        "detect_language": False,
        "label": "Speaker Diarization",
    }
    if profile == "multilingual":
        cfg.update(
            {
                "language": "multi",
                "diarize": False,
                "detect_language": False,
                "label": "Language Switching / Multilingual Transcription",
            }
        )
    elif profile == "auto_language_detection":
        cfg.update(
            {
                "language": "multi",
                "diarize": False,
                "detect_language": True,
                "label": "Auto Language Detection",
            }
        )
    elif profile == "multilingual_diarization":
        cfg.update(
            {
                "language": "multi",
                "diarize": True,
                "detect_language": False,
                "label": "Streaming Multilingual + Diarization",
            }
        )
    return cfg


def _fetch_elevenlabs_voices(*, api_key: str, timeout_secs: float = 10.0) -> list[dict[str, Any]]:
    import urllib.request

    if not str(api_key or "").strip():
        raise RuntimeError("Missing ELEVENLABS_API_KEY.")
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/voices",
        headers={
            "xi-api-key": str(api_key).strip(),
            "accept": "application/json",
            "user-agent": "VoiceGameMaster/2.0",
        },
    )
    with urllib.request.urlopen(req, timeout=float(timeout_secs)) as resp:  # noqa: S310 - fixed URL
        payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
    voices = payload.get("voices") if isinstance(payload, dict) else None
    out: list[dict[str, Any]] = []
    for v in voices if isinstance(voices, list) else []:
        if not isinstance(v, dict):
            continue
        voice_id = str(v.get("voice_id") or "").strip()
        name = str(v.get("name") or "").strip()
        if not voice_id:
            continue
        labels = v.get("labels") if isinstance(v.get("labels"), dict) else {}
        category = str(v.get("category") or "").strip()
        out.append(
            {
                "voice_id": voice_id,
                "name": name or voice_id,
                "category": category,
                "labels": labels,
            }
        )
    out.sort(key=lambda x: str(x.get("name") or "").lower())
    return out


def _fetch_elevenlabs_subscription(*, api_key: str, timeout_secs: float = 8.0) -> dict[str, Any]:
    import urllib.request

    if not str(api_key or "").strip():
        raise RuntimeError("Missing ELEVENLABS_API_KEY.")
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/user/subscription",
        headers={
            "xi-api-key": str(api_key).strip(),
            "accept": "application/json",
            "user-agent": "VoiceGameMaster/2.0",
        },
    )
    with urllib.request.urlopen(req, timeout=float(timeout_secs)) as resp:  # noqa: S310 - fixed URL
        payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected ElevenLabs subscription payload.")
    return payload


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

_ELEVENLABS_WEBSOCKET_SUPPORTED_MODELS = {
    "eleven_turbo_v2_5",
    "eleven_flash_v2_5",
}


def _normalize_openai_tts_model(model: str | None) -> str:
    m = str(model or "").strip()
    if not m.lower().startswith("gpt-"):
        return "gpt-4o-mini-tts"
    return m


def _normalize_openai_tts_voice(voice: str | None) -> str:
    v = str(voice or "").strip().lower()
    if v in _OPENAI_TTS_VOICE_PRESETS:
        return v
    return "alloy"


def _resolve_elevenlabs_voice_id(*, api_key: str, requested_voice: str) -> str:
    requested = str(requested_voice or "").strip()
    voices = _fetch_elevenlabs_voices(api_key=api_key)
    if not voices:
        if requested:
            return requested
        raise RuntimeError("No ElevenLabs voices found for this API key.")

    by_id = {str(v.get("voice_id") or "").strip(): v for v in voices}
    by_name = {str(v.get("name") or "").strip().lower(): str(v.get("voice_id") or "").strip() for v in voices}

    if requested and requested in by_id:
        return requested
    if requested:
        by_name_id = by_name.get(requested.lower())
        if by_name_id:
            return by_name_id
        # Common migration path: switching providers while an OpenAI preset voice is still stored.
        if requested.lower() in _OPENAI_TTS_VOICE_PRESETS:
            return str(voices[0].get("voice_id") or "").strip()
        # Keep runtime stable: if the saved ID isn't available for this key, use a valid
        # account voice instead of looping on websocket reconnect errors.
        print(
            "[elevenlabs] Warning: configured voice ID is not in the fetched voice list; "
            "falling back to the first available voice for this account."
        )
        return str(voices[0].get("voice_id") or "").strip()
    return str(voices[0].get("voice_id") or "").strip()


def _build_elevenlabs_tts_service(*, api_key: str, model: str, voice_id: str):
    try:
        import pipecat.services.elevenlabs.tts as elevenlabs_tts_mod  # type: ignore
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService  # type: ignore
    except ModuleNotFoundError:
        # Compatibility with package layouts that export the class from the package root.
        import pipecat.services.elevenlabs as elevenlabs_tts_mod  # type: ignore
        from pipecat.services.elevenlabs import ElevenLabsTTSService  # type: ignore

    class FixedElevenLabsTTSService(ElevenLabsTTSService):  # type: ignore[misc]
        """Work around Pipecat 0.0.103 dropping `audio` when `isFinal=true` in the same message."""

        async def _receive_messages(self):
            trace_ws = str(os.environ.get("GM_ELEVENLABS_TRACE") or "").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            async for message in self._get_websocket():
                msg = json.loads(message)

                received_ctx_id = msg.get("contextId")
                if trace_ws:
                    elevenlabs_tts_mod.logger.debug(
                        f"[el_ws] msg ctx={received_ctx_id} final={bool(msg.get('isFinal'))} "
                        f"has_audio={bool(msg.get('audio'))} has_alignment={bool(msg.get('alignment'))}"
                    )

                # Keep original context-availability behavior.
                if not self.audio_context_available(received_ctx_id):
                    if self.get_active_audio_context_id() == received_ctx_id:
                        elevenlabs_tts_mod.logger.debug(
                            f"Received a delayed message, recreating the context: {received_ctx_id}"
                        )
                        await self.create_audio_context(received_ctx_id)
                    else:
                        elevenlabs_tts_mod.logger.debug(
                            f"Ignoring message from unavailable context: {received_ctx_id}"
                        )
                        continue

                # Process payload first; ElevenLabs can set isFinal=true on a frame that
                # still carries valid audio/alignment data.
                if msg.get("audio"):
                    await self.stop_ttfb_metrics()
                    await self.start_word_timestamps()

                    audio = base64.b64decode(msg["audio"])
                    if trace_ws:
                        elevenlabs_tts_mod.logger.debug(
                            f"[el_ws] audio bytes={len(audio)} sample_rate={self.sample_rate} ctx={received_ctx_id}"
                        )
                    frame = elevenlabs_tts_mod.TTSAudioRawFrame(
                        audio, self.sample_rate, 1, context_id=received_ctx_id
                    )
                    await self.append_to_audio_context(received_ctx_id, frame)
                    seen = getattr(self, "_el_audio_seen_context_ids", None)
                    if seen is None:
                        seen = set()
                        setattr(self, "_el_audio_seen_context_ids", seen)
                    seen.add(str(received_ctx_id))

                if msg.get("alignment"):
                    alignment = msg["alignment"]
                    word_times, self._partial_word, self._partial_word_start_time = (
                        elevenlabs_tts_mod.calculate_word_times(
                            alignment,
                            self._cumulative_time,
                            self._partial_word,
                            self._partial_word_start_time,
                        )
                    )

                    if word_times:
                        await self.add_word_timestamps(word_times, received_ctx_id)

                        char_start_times_ms = alignment.get("charStartTimesMs", [])
                        char_durations_ms = alignment.get("charDurationsMs", [])

                        if char_start_times_ms and char_durations_ms:
                            chunk_end_time_ms = char_start_times_ms[-1] + char_durations_ms[-1]
                            chunk_end_time_seconds = chunk_end_time_ms / 1000.0
                            self._cumulative_time += chunk_end_time_seconds
                        else:
                            self._cumulative_time = word_times[-1][1]
                            elevenlabs_tts_mod.logger.warning(
                                "_receive_messages: using fallback timing method - "
                                "consider investigating alignment data structure"
                            )

                if msg.get("isFinal") is True:
                    seen = getattr(self, "_el_audio_seen_context_ids", None)
                    has_seen_audio = bool(seen and str(received_ctx_id) in seen)
                    if not has_seen_audio and not msg.get("audio") and not msg.get("alignment"):
                        await self.push_error(
                            error_msg=(
                                "ElevenLabs returned empty audio for this turn. "
                                "Check ELEVENLABS_API_KEY, selected voice_id, and character quota."
                            )
                        )
                    elevenlabs_tts_mod.logger.trace(
                        f"Received final message for context {received_ctx_id}"
                    )
                    if seen is not None:
                        try:
                            seen.discard(str(received_ctx_id))
                        except Exception:
                            pass
                    continue

    errors: list[str] = []
    attempts: list[dict[str, Any]] = [
        {
            "api_key": api_key,
            "model": model,
            "voice_id": voice_id,
            # Our pipeline emits complete text chunks; sentence aggregation can stall output.
            "aggregate_sentences": False,
        },
        {
            "api_key": api_key,
            "model": model,
            "voice": voice_id,
            "aggregate_sentences": False,
        },
        {"api_key": api_key, "model": model, "voice_id": voice_id},
        {"api_key": api_key, "model": model, "voice": voice_id},
        {"api_key": api_key, "model_id": model, "voice_id": voice_id, "aggregate_sentences": False},
        {"api_key": api_key, "model_id": model, "voice": voice_id, "aggregate_sentences": False},
    ]

    # Newer Pipecat builds may require input params object instead of voice_id/voice kwargs.
    try:
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSServiceInputParams as params_cls  # type: ignore
    except Exception:
        params_cls = None
    if params_cls is not None:
        try:
            params = params_cls(voice_id=voice_id)
            attempts.insert(
                0,
                {
                    "api_key": api_key,
                    "model": model,
                    "params": params,
                    "aggregate_sentences": False,
                },
            )
            attempts.insert(
                1,
                {
                    "api_key": api_key,
                    "model_id": model,
                    "params": params,
                    "aggregate_sentences": False,
                },
            )
        except Exception as e:
            errors.append(f"input-params-init failed: {e}")

    for kwargs in attempts:
        try:
            return FixedElevenLabsTTSService(**kwargs)
        except TypeError as e:
            errors.append(f"{kwargs}: {e}")
            continue

    msg = "; ".join(errors[-4:]) if errors else "unknown constructor mismatch"
    raise RuntimeError(f"Failed to initialize ElevenLabs TTS service ({msg}).")


async def run_voice_ws_mode(
    *,
    controller: RLMController,
    root: Path,
    settings_store: SettingsStore,
    state: WorldStateStore,
    knowledge_manager: KnowledgeManager,
) -> None:
    try:
        from pipecat.pipeline.pipeline import Pipeline  # type: ignore
        from pipecat.pipeline.runner import PipelineRunner  # type: ignore
        from pipecat.pipeline.task import PipelineParams, PipelineTask  # type: ignore
        from pipecat.services.openai.stt import OpenAISTTService  # type: ignore
        from pipecat.services.openai.tts import OpenAITTSService  # type: ignore
        from pipecat.transcriptions.language import Language  # type: ignore
        from pipecat.transports.websocket.server import (  # type: ignore
            WebsocketServerParams,
            WebsocketServerTransport,
        )
        from pipecat.transports import base_input as pipecat_base_input  # type: ignore
    except ModuleNotFoundError as e:  # pragma: no cover
        raise RuntimeError(
            "Pipecat is not installed in this environment.\n"
            "Voice mode requires Python <3.14 due to pipecat-ai's numba dependency.\n"
            "Use the included .venv-voice (Python 3.13) or create one with uv:\n"
            "  ./.venv/bin/uv venv -p 3.13 .venv-voice\n"
            "  ./.venv-voice/bin/python -m ensurepip --upgrade\n"
            "  ./.venv-voice/bin/python -m pip install -U pip\n"
            "  ./.venv-voice/bin/python -m pip install -e '.[voice,knowledge]'\n"
        ) from e

    from gm_engine.interaction.bot_speaking_state import BotSpeakingState, BotSpeakingStateProcessor
    from gm_engine.interaction.control_processor import ControlProcessor
    from gm_engine.interaction.pipecat_rlm_processor import RLMProcessor
    from gm_engine.interaction.pipecat_ws_serializer import SimpleJSONFrameSerializer

    # If someone opens the WS endpoint in a browser, websockets will log a stack trace by default.
    # Keep logs readable by demoting these handshake failures.
    import logging

    logging.getLogger("websockets.server").setLevel(logging.CRITICAL)

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Missing OPENAI_API_KEY. Put it in `.env` (see `.env.example`) or export it.")

    # Our browser client sends audio only while PTT is held. Avoid noisy warnings.
    pipecat_base_input.AUDIO_INPUT_TIMEOUT_SECS = 300.0

    settings = settings_store.get()

    ws_host = settings.voice.ws_host
    ws_port = settings.voice.ws_port
    ws_session_timeout_secs = int(settings.voice.ws_session_timeout_secs)
    http_host = settings.voice.http_host
    http_port = settings.voice.http_port

    # Auto-heal common startup failures: kill stale listeners holding configured ports.
    # Disable with GM_KILL_CONFLICTING_PORTS=0 if you prefer manual process control.
    if _is_truthy_env("GM_KILL_CONFLICTING_PORTS", "1"):
        protected = {os.getpid(), os.getppid()}
        _free_port_by_terminating_listeners(int(ws_port), label="websocket", protected_pids=protected)
        if int(http_port) > 0 and int(http_port) != int(ws_port):
            _free_port_by_terminating_listeners(int(http_port), label="http", protected_pids=protected)

    stt_provider = str(settings.openai.stt_provider or "openai").strip().lower()
    tts_provider = str(settings.openai.tts_provider or "openai").strip().lower()
    stt_model = settings.openai.stt_model
    tts_model = settings.openai.tts_model
    tts_voice = settings.openai.tts_voice
    llm_model = settings.openai.llm_model
    stt_language_label = _normalize_locale_tag(settings.voice.locale)
    dg_feature_label = ""
    dg_diarize_enabled = False
    dg_detect_language_enabled = False

    client_dir = root / "docs" / "voice_client"
    httpd = None
    if client_dir.exists():
        try_ports = [http_port] if http_port != 0 else [0]
        if http_port not in (0,):
            try_ports.extend(range(http_port + 1, http_port + 21))

        for p in try_ports:
            try:
                httpd = _make_http_server(
                    client_dir,
                    http_host,
                    p,
                    health_payload=lambda: {
                        "ok": True,
                        "service": "voice-gm",
                        "ui": {"host": http_host, "port": http_port},
                        "ws": {"host": ws_host, "port": ws_port, "session_timeout_secs": ws_session_timeout_secs},
                        "openai_api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
                        "deepgram_api_key_present": bool(os.environ.get("DEEPGRAM_API_KEY")),
                        "elevenlabs_api_key_present": bool(os.environ.get("ELEVENLABS_API_KEY")),
                    },
                    settings_store=settings_store,
                    controller=controller,
                    knowledge_manager=knowledge_manager,
                    state=state,
                    loop=asyncio.get_running_loop(),
                )
                http_port = int(httpd.server_address[1])
                break
            except OSError:
                continue

        if httpd:
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
        else:
            print(
                f"Warning: could not start the client UI HTTP server on {http_host}:{settings.voice.http_port} "
                "(port in use?)."
            )

    serializer = SimpleJSONFrameSerializer()
    transport = WebsocketServerTransport(
        WebsocketServerParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            # Smaller output chunks reduce barge-in latency.
            audio_out_10ms_chunks=1,
            serializer=serializer,  # type: ignore[arg-type]
            # Close stale dead websocket clients so reconnects stay reliable.
            session_timeout=ws_session_timeout_secs,
        ),
        host=ws_host,
        port=ws_port,
    )

    if stt_provider == "deepgram":
        try:
            from deepgram import LiveOptions  # type: ignore
        except ModuleNotFoundError as e:  # pragma: no cover
            raise RuntimeError(
                "Deepgram SDK is not installed. Install voice extras with Deepgram support:\n"
                "  python -m pip install -e '.[voice]'\n"
            ) from e

        try:
            from gm_engine.interaction.deepgram_stt import DeepgramNovaDiarizationSTTService
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "Deepgram STT support is unavailable. Install voice extras with Deepgram support:\n"
                "  python -m pip install -e '.[voice]'\n"
            ) from e

        deepgram_api_key = os.environ.get("DEEPGRAM_API_KEY")
        if not deepgram_api_key:
            raise RuntimeError("Missing DEEPGRAM_API_KEY for Deepgram STT.")

        dg_cfg = _resolve_deepgram_live_options(settings)
        dg_model = str(dg_cfg.get("model") or "nova-3-general").strip() or "nova-3-general"
        dg_language = str(dg_cfg.get("language") or "multi").strip() or "multi"
        dg_diarize = bool(dg_cfg.get("diarize", False))
        dg_detect_language = bool(dg_cfg.get("detect_language", False))
        dg_feature_label = str(dg_cfg.get("label") or "")
        dg_diarize_enabled = dg_diarize
        dg_detect_language_enabled = dg_detect_language
        stt_language_label = f"{dg_language}{' (detect)' if dg_detect_language else ''}"

        live_kwargs: dict[str, Any] = {
            "encoding": "linear16",
            "model": dg_model,
            "language": dg_language,
            "channels": 1,
            "interim_results": True,
            "smart_format": True,
            "punctuate": True,
            "profanity_filter": False,
            "diarize": dg_diarize,
            "vad_events": False,
        }
        if dg_detect_language:
            live_kwargs["detect_language"] = True
        try:
            live_options = LiveOptions(**live_kwargs)
        except TypeError:
            # Backward compatibility with Deepgram SDKs that don't expose detect_language.
            live_kwargs.pop("detect_language", None)
            live_options = LiveOptions(**live_kwargs)

        stt = DeepgramNovaDiarizationSTTService(
            api_key=deepgram_api_key,
            live_options=live_options,
            sample_rate=16000,
        )
        stt_model = dg_model
    else:
        stt_language = _resolve_pipecat_language_for_locale(settings.voice.locale, Language)
        if stt_language is None:
            stt_language = Language.EN
        stt_language_label = str(getattr(stt_language, "value", stt_language) or "en")
        stt = OpenAISTTService(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            model=stt_model,
            language=stt_language,
        )
    if tts_provider == "elevenlabs":
        try:
            # Import check only. Actual instance creation is done by compatibility helper below.
            import pipecat.services.elevenlabs.tts  # type: ignore # noqa: F401
        except ModuleNotFoundError:
            try:
                import pipecat.services.elevenlabs  # type: ignore # noqa: F401
            except ModuleNotFoundError as e:  # pragma: no cover
                raise RuntimeError(
                    "ElevenLabs TTS support is unavailable. Install voice extras with ElevenLabs support:\n"
                    "  python -m pip install -e '.[voice]'\n"
                ) from e

        el_key = str(os.environ.get("ELEVENLABS_API_KEY") or "").strip()
        if not el_key:
            raise RuntimeError("Missing ELEVENLABS_API_KEY for ElevenLabs TTS.")
        try:
            sub = _fetch_elevenlabs_subscription(api_key=el_key)
            used_raw = sub.get("character_count")
            limit_raw = sub.get("character_limit")
            tier = str(sub.get("tier") or "unknown").strip()
            used = int(used_raw) if used_raw is not None else None
            limit = int(limit_raw) if limit_raw is not None else None
            if limit is not None and used is not None:
                remaining = max(0, int(limit) - int(used))
                print(f"[elevenlabs] Subscription: tier={tier}, chars_used={used}/{limit}, remaining={remaining}")
                if remaining <= 0:
                    if os.environ.get("OPENAI_API_KEY"):
                        print(
                            "[elevenlabs] Warning: character quota exhausted; "
                            "falling back to OpenAI TTS for this run."
                        )
                        tts_provider = "openai"
                        if not str(tts_model or "").strip().startswith("gpt-"):
                            tts_model = "gpt-4o-mini-tts"
                        if str(tts_voice or "").strip() not in _OPENAI_TTS_VOICE_PRESETS:
                            tts_voice = "alloy"
                    else:
                        raise RuntimeError(
                            "ElevenLabs character quota is exhausted and OPENAI_API_KEY is missing for fallback."
                        )
        except RuntimeError:
            raise
        except Exception as e:
            print(f"[elevenlabs] Warning: could not verify subscription/quota ({e}). Continuing.")
    if tts_provider == "elevenlabs":
        el_key = str(os.environ.get("ELEVENLABS_API_KEY") or "").strip()
        if not el_key:
            raise RuntimeError("Missing ELEVENLABS_API_KEY for ElevenLabs TTS.")
        el_model = str(tts_model or "").strip()
        if not el_model or el_model.startswith("gpt-"):
            el_model = "eleven_turbo_v2_5"
        if el_model.lower() not in _ELEVENLABS_WEBSOCKET_SUPPORTED_MODELS:
            prev = el_model
            # Keep runtime stable on Pipecat websocket transport.
            el_model = "eleven_turbo_v2_5"
            print(
                "[elevenlabs] Warning: model "
                f"'{prev}' is not supported reliably by this websocket runtime; "
                f"using '{el_model}' instead.",
                flush=True,
            )
            try:
                settings_store.update({"openai": {"tts_model": el_model}})
            except Exception:
                pass
        requested_voice = str(tts_voice or "").strip()
        el_voice = _resolve_elevenlabs_voice_id(api_key=el_key, requested_voice=requested_voice)
        if requested_voice and requested_voice != el_voice:
            print(
                f"[elevenlabs] Using voice_id '{el_voice}' (requested '{requested_voice}' was not a valid ElevenLabs ID)."
            )
        tts = _build_elevenlabs_tts_service(api_key=el_key, model=el_model, voice_id=el_voice)
        tts_model = el_model
        tts_voice = el_voice
    else:
        req_model = str(tts_model or "").strip()
        req_voice = str(tts_voice or "").strip()
        tts_model = _normalize_openai_tts_model(req_model)
        tts_voice = _normalize_openai_tts_voice(req_voice)
        if req_model != tts_model:
            print(
                f"[openai-tts] Warning: invalid OpenAI TTS model '{req_model}'. "
                f"Using '{tts_model}'.",
                flush=True,
            )
            try:
                settings_store.update({"openai": {"tts_model": tts_model}})
            except Exception:
                pass
        if req_voice.lower() != tts_voice:
            print(
                f"[openai-tts] Warning: invalid OpenAI TTS voice '{req_voice}'. "
                f"Using '{tts_voice}'.",
                flush=True,
            )
            try:
                settings_store.update({"openai": {"tts_voice": tts_voice}})
            except Exception:
                pass
        tts = OpenAITTSService(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            model=tts_model,
            voice=tts_voice,
            # We already send complete chunks; sentence aggregation can get stuck on quotes or missing lookahead.
            aggregate_sentences=False,
        )
    bot_speaking_state = BotSpeakingState()
    control = ControlProcessor(
        settings_store=settings_store,
        state=state,
        knowledge=knowledge_manager,
        controller=controller,
        barge_in_state=bot_speaking_state,
    )
    rlm = RLMProcessor(
        controller=controller,
        settings_store=settings_store,
    )
    bot_speaking = BotSpeakingStateProcessor(state=bot_speaking_state)

    pipeline = Pipeline([transport.input(), control, stt, rlm, tts, bot_speaking, transport.output()])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            allow_interruptions=True,
        ),
        # Voice server should wait indefinitely for clients/utterances.
        idle_timeout_secs=None,
    )

    print("")
    print("Voice config:")
    print(f"- OpenAI base_url: {(os.environ.get('OPENAI_BASE_URL') or '<default>')}")
    print(f"- LLM provider: {str(settings.openai.llm_provider or 'openai')}")
    print(f"- LLM model: {llm_model}")
    print(f"- STT provider: {stt_provider}")
    print(f"- STT model: {stt_model}")
    print(f"- STT language: {stt_language_label}")
    if stt_provider == "deepgram":
        if dg_feature_label:
            print(f"- STT Deepgram feature: {dg_feature_label}")
        print(f"- STT diarization: {'enabled' if dg_diarize_enabled else 'disabled'}")
        print(f"- STT detect_language: {'enabled' if dg_detect_language_enabled else 'disabled'}")
    print(f"- TTS provider: {tts_provider}")
    print(f"- TTS model: {tts_model}")
    print(f"- TTS voice: {tts_voice}")
    print(f"- Campaign: {settings.voice.campaign_id}")
    print(f"- Session: {settings.voice.session_id}")
    print(f"- Player: {settings.voice.player_id}")
    print(f"- Locale: {settings.voice.locale}")
    print(f"- Knowledge enabled: {bool(settings.knowledge.enabled)}")
    print(f"- Knowledge backend: {settings.knowledge.backend}")
    print(f"- WS session timeout: {ws_session_timeout_secs}s")
    if settings.knowledge.backend == "local":
        print(f"- Knowledge local_path: {settings.knowledge.local_path}")
    else:
        print(f"- Knowledge qdrant_url: {settings.knowledge.qdrant_url}")
    if settings.knowledge.split_collections:
        print(
            f"- Knowledge collections: game={settings.knowledge.game_collection}, "
            f"guidance={settings.knowledge.guidance_collection}"
        )
    else:
        print(f"- Knowledge collection: {settings.knowledge.collection}")
    print("")
    if ws_host in ("localhost", "127.0.0.1") or http_host in ("localhost", "127.0.0.1"):
        print("Tip: set WS/UI host to 0.0.0.0 in the Settings tab if you want to open the UI from another device.")
        print("")
    print("Voice WS mode running:")
    for u in _pretty_http_urls(http_host, http_port):
        print(f"- Client UI: {u}")
        print(f"- Health: {u.rstrip('/')}/health")
    for u in _pretty_ws_urls(ws_host, ws_port):
        print(f"- WebSocket: {u}")
    print("")
    print("Open the Client UI URL in your browser (HTTP).")
    print("Do NOT open the WebSocket URL directly in your browser address bar.")
    print("Mic note: most browsers allow the microphone on http://localhost, but block it on http://<LAN-IP> unless you use HTTPS.")
    print("")
    print("In the Client UI, use Push-to-talk or Auto VAD to send an utterance.")
    print("Ctrl+C to stop.")
    print("")

    runner = PipelineRunner()
    await runner.run(task)


def main():
    _try_load_dotenv()

    root = Path(__file__).resolve().parents[1]
    (root / "data").mkdir(parents=True, exist_ok=True)
    settings_store = SettingsStore(path=root / "data" / "settings.json")
    stored = settings_store.get()
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["text", "voice-ws"], default="text")
    ap.add_argument("--campaign-id", default=stored.voice.campaign_id)
    ap.add_argument("--session-id", default=stored.voice.session_id)
    ap.add_argument("--player-id", default=stored.voice.player_id)
    ap.add_argument("--locale", default=stored.voice.locale)
    ap.add_argument("--ws-host", default=stored.voice.ws_host)
    ap.add_argument("--ws-port", type=int, default=int(stored.voice.ws_port))
    ap.add_argument("--http-host", default=stored.voice.http_host)
    ap.add_argument("--http-port", type=int, default=int(stored.voice.http_port))
    args = ap.parse_args()

    # Persist CLI overrides so the GUI and runtime always agree.
    voice_patch: dict[str, object] = {}
    if args.campaign_id != stored.voice.campaign_id:
        voice_patch["campaign_id"] = args.campaign_id
    if args.session_id != stored.voice.session_id:
        voice_patch["session_id"] = args.session_id
    if args.player_id != stored.voice.player_id:
        voice_patch["player_id"] = args.player_id
    if args.locale != stored.voice.locale:
        voice_patch["locale"] = args.locale
    if args.ws_host != stored.voice.ws_host:
        voice_patch["ws_host"] = args.ws_host
    if int(args.ws_port) != int(stored.voice.ws_port):
        voice_patch["ws_port"] = int(args.ws_port)
    if args.http_host != stored.voice.http_host:
        voice_patch["http_host"] = args.http_host
    if int(args.http_port) != int(stored.voice.http_port):
        voice_patch["http_port"] = int(args.http_port)
    if voice_patch:
        settings_store.update({"voice": voice_patch})

    # Refresh local settings snapshot after applying CLI overrides.
    stored = settings_store.get()

    state = WorldStateStore(db_path=root / "data" / "world.sqlite")
    logger = EventLogger(path=root / "data" / "events.jsonl")

    # Knowledge is optional; install `.[knowledge]` and run Qdrant to enable.
    knowledge = NullKnowledgeStore()
    qdrant_store = None
    try:
        from qdrant_client import QdrantClient  # type: ignore

        from gm_engine.knowledge.embeddings import OpenAIEmbedder
        from gm_engine.knowledge.qdrant_store import QdrantStore  # type: ignore
        from gm_engine.knowledge.routed_store import RoutedQdrantStore

        s = settings_store.get()
        embedder = OpenAIEmbedder(
            model=s.openai.embedding_model,
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
        )
        if s.knowledge.backend == "local":
            local_dir = (root / str(s.knowledge.local_path)).resolve()
            local_dir.mkdir(parents=True, exist_ok=True)
            client = QdrantClient(path=str(local_dir), force_disable_check_same_thread=True)
        else:
            client = QdrantClient(url=s.knowledge.qdrant_url)

        if s.knowledge.split_collections:
            qdrant_store = RoutedQdrantStore(
                game=QdrantStore(
                    client=client,
                    embedder=embedder,
                    collection=s.knowledge.game_collection,
                ),
                guidance=QdrantStore(
                    client=client,
                    embedder=embedder,
                    collection=s.knowledge.guidance_collection,
                ),
            )
        else:
            qdrant_store = QdrantStore(
                client=client,
                embedder=embedder,
                collection=s.knowledge.collection,
            )
        knowledge = qdrant_store
    except ModuleNotFoundError:
        pass
    except Exception as e:
        print(f"Warning: knowledge backend disabled: {e}")

    knowledge_manager = KnowledgeManager(root=root, settings_store=settings_store, qdrant=qdrant_store)

    # Best-effort startup sync from GUI settings (path/doc/ruleset + activate/ingest flags).
    startup_settings = settings_store.get()
    if _should_sync_primary_rulebook(startup_settings):
        try:
            sync_res = asyncio.run(
                knowledge_manager.sync_primary_rulebook(
                    ingest=bool(startup_settings.knowledge.primary_rulebook_auto_ingest),
                    activate=bool(startup_settings.knowledge.primary_rulebook_auto_activate),
                )
            )
            print(
                "Primary rulebook synced: "
                f"{sync_res.get('doc_id')} ({sync_res.get('status')}) "
                f"from {sync_res.get('path')}"
            )
            if sync_res.get("ingest_skipped_reason"):
                print(f"Primary rulebook ingest skipped: {sync_res.get('ingest_skipped_reason')}")
        except Exception as e:
            print(f"Warning: primary rulebook sync failed: {e}")

    llm = _build_llm_provider(settings_store=settings_store)

    controller = RLMController(
        llm=llm,
        state=state,
        knowledge=knowledge,
        logger=logger,
        settings_store=settings_store,
    )

    try:
        if args.mode == "text":
            adapter = PipecatAdapter(controller=controller, tts=DummyStreamingTTS())
            asyncio.run(run_text_mode(adapter))
        elif args.mode == "voice-ws":
            asyncio.run(
                run_voice_ws_mode(
                    controller=controller,
                    root=root,
                    settings_store=settings_store,
                    state=state,
                    knowledge_manager=knowledge_manager,
                )
            )
    except RuntimeError as e:
        print(str(e))
        raise SystemExit(1)
    finally:
        # Avoid noisy __del__ warnings from qdrant-client during interpreter shutdown.
        try:
            if qdrant_store is not None:
                client = getattr(qdrant_store, "client", None)
                if client is None and hasattr(qdrant_store, "game"):
                    client = getattr(getattr(qdrant_store, "game"), "client", None)
                if client is not None:
                    client.close()  # type: ignore[no-any-return]
        except Exception:
            pass


if __name__ == "__main__":
    main()
