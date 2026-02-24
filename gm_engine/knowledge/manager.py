from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable, Protocol

from gm_engine.app.settings_store import SettingsStore
from gm_engine.rlm.types import RetrievalSpec, TurnContext


ProgressCB = Callable[[dict[str, Any]], Awaitable[None]]


class VectorStoreLike(Protocol):
    embedder: Any

    async def search(self, ctx: TurnContext, spec: RetrievalSpec) -> list[dict]: ...

    async def upsert_points(self, points: list[Any]) -> None: ...

    async def delete_by_filter(self, *, filters: dict[str, Any]) -> None: ...


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "documents": []}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and isinstance(obj.get("documents"), list):
            return obj
    except Exception:
        pass
    return {"version": 1, "documents": []}


def _slug_doc_id(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip().lower()).strip("_")
    return s[:64] or "rulebook"


def _resolve_repo_path(root: Path, path: str) -> Path:
    p = Path(str(path or "").strip()).expanduser()
    if not p.is_absolute():
        p = root / p
    return p.resolve()


@dataclass
class _Upload:
    upload_id: str
    filename: str
    doc_id: str
    ruleset: str | None
    doc_kind: str | None
    collection_target: str | None
    path: Path
    total_bytes: int | None = None
    received_bytes: int = 0
    created_at: float = field(default_factory=time.time)


@dataclass
class KnowledgeManager:
    root: Path
    settings_store: SettingsStore
    qdrant: VectorStoreLike | None

    _uploads: dict[str, _Upload] = field(default_factory=dict, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def uploads_dir(self) -> Path:
        return self.root / "data" / "uploads"

    @property
    def index_path(self) -> Path:
        return self.root / "data" / "knowledge_index.json"

    async def list_documents(self) -> list[dict[str, Any]]:
        async with self._lock:
            idx = _load_index(self.index_path)
            return list(idx.get("documents") or [])

    async def search(self, ctx: TurnContext, spec: RetrievalSpec) -> list[dict]:
        if not self.qdrant:
            return []
        return await self.qdrant.search(ctx, spec)

    async def register_local_pdf(
        self,
        *,
        path: str,
        doc_id: str | None = None,
        ruleset: str | None = None,
        doc_kind: str | None = None,
        collection_target: str | None = None,
    ) -> dict[str, Any]:
        path_s = str(path or "").strip()
        if not path_s:
            raise RuntimeError("Primary rulebook path is empty.")

        pdf_path = _resolve_repo_path(self.root, path_s)
        if not pdf_path.exists() or not pdf_path.is_file():
            raise RuntimeError(f"Rulebook file not found: {pdf_path}")

        did = _slug_doc_id(doc_id or pdf_path.stem)
        kind = str(doc_kind or "rulebook").strip().lower() or "rulebook"
        rules = str(ruleset).strip().lower() if ruleset is not None and str(ruleset).strip() else None
        chosen_target = self._collection_target(kind, explicit=collection_target)

        st = pdf_path.stat()
        file_size = int(st.st_size)
        file_mtime = int(st.st_mtime)

        async with self._lock:
            idx = _load_index(self.index_path)
            docs = list(idx.get("documents") or [])
            prev = next((d for d in docs if d.get("doc_id") == did), None)

            unchanged = False
            if isinstance(prev, dict):
                try:
                    prev_size = int(prev.get("file_size")) if prev.get("file_size") is not None else None
                    prev_mtime = int(prev.get("file_mtime")) if prev.get("file_mtime") is not None else None
                except Exception:
                    prev_size = None
                    prev_mtime = None
                unchanged = (
                    str(prev.get("path") or "") == str(pdf_path)
                    and prev_size == file_size
                    and prev_mtime == file_mtime
                )

            status = "uploaded"
            if unchanged and isinstance(prev, dict):
                prev_status = str(prev.get("status") or "").strip().lower()
                if prev_status and prev_status != "ingesting":
                    status = prev_status

            created_at = time.time()
            if isinstance(prev, dict) and prev.get("created_at") is not None:
                try:
                    created_at = float(prev.get("created_at"))
                except Exception:
                    created_at = time.time()

            doc: dict[str, Any] = {
                "doc_id": did,
                "filename": pdf_path.name,
                "ruleset": rules,
                "doc_kind": kind,
                "collection_target": chosen_target,
                "path": str(pdf_path),
                "status": status,
                "file_size": file_size,
                "file_mtime": file_mtime,
                "created_at": created_at,
                "source": "local_file",
            }
            if unchanged and isinstance(prev, dict):
                if prev.get("chunks") is not None:
                    doc["chunks"] = prev.get("chunks")
                if isinstance(prev.get("type_counts"), dict):
                    doc["type_counts"] = dict(prev.get("type_counts") or {})
                if prev.get("error") is not None:
                    doc["error"] = prev.get("error")

            docs = [d for d in docs if d.get("doc_id") != did]
            docs.append(doc)
            idx["documents"] = docs
            _atomic_write_json(self.index_path, idx)

        return doc

    async def sync_primary_rulebook(
        self,
        *,
        progress_cb: ProgressCB | None = None,
        ingest: bool | None = None,
        activate: bool | None = None,
    ) -> dict[str, Any]:
        s = self.settings_store.get()
        k = s.knowledge
        source = str(k.primary_rulebook_source or "path").strip().lower()
        if source not in {"path", "doc"}:
            source = "path"

        do_ingest = bool(k.primary_rulebook_auto_ingest if ingest is None else ingest)
        do_activate = bool(k.primary_rulebook_auto_activate if activate is None else activate)

        async def emit(payload: dict[str, Any]) -> None:
            if progress_cb:
                await progress_cb(payload)

        await emit({"type": "kb_rulebook_sync_status", "status": "starting"})

        doc: dict[str, Any] | None = None
        doc_id = ""
        if source == "doc":
            chosen_doc_id = str(k.primary_rulebook_doc_choice or k.primary_rulebook_doc_id or "").strip()
            if not chosen_doc_id:
                raise RuntimeError("Select a rulebook document from dropdown or upload one first.")
            docs = await self.list_documents()
            doc = next((d for d in docs if str(d.get("doc_id") or "").strip() == chosen_doc_id), None)
            if doc is None:
                # Compatibility recovery: older settings may store truncated doc ids.
                prefix = f"{chosen_doc_id}_"
                candidates = [d for d in docs if str(d.get("doc_id") or "").strip().startswith(prefix)]
                if candidates:
                    candidates.sort(
                        key=lambda d: float(d.get("created_at") or 0.0),
                        reverse=True,
                    )
                    doc = candidates[0]
            if doc is None:
                raise RuntimeError(f"Selected rulebook doc_id not found: {chosen_doc_id}")
            doc_id = str(doc.get("doc_id") or "").strip()
            if not doc_id:
                raise RuntimeError("Selected rulebook doc_id is empty.")
        else:
            doc = await self.register_local_pdf(
                path=k.primary_rulebook_path,
                doc_id=k.primary_rulebook_doc_id,
                ruleset=k.primary_rulebook_ruleset or None,
                doc_kind=k.primary_rulebook_doc_kind or "rulebook",
                collection_target=k.primary_rulebook_collection_target or None,
            )
            doc_id = str(doc.get("doc_id") or "")
            if not doc_id:
                raise RuntimeError("Primary rulebook could not be registered.")

        if do_activate:
            active = [str(x or "").strip() for x in (k.active_doc_ids or []) if str(x or "").strip()]
            if doc_id not in active:
                active = active + [doc_id]
            self.settings_store.update({"knowledge": {"enabled": True, "active_doc_ids": active}})

        ingested = False
        ingest_skipped_reason = ""
        if do_ingest:
            if self.qdrant is None:
                ingest_skipped_reason = "Knowledge backend is disabled (Qdrant not configured)."
            else:
                status = str((doc or {}).get("status") or "").strip().lower()
                if status != "ready":
                    await self.ingest_doc(
                        doc_id=doc_id,
                        progress_cb=progress_cb,
                        replace_existing=True,
                    )
                    ingested = True
        docs = await self.list_documents()
        latest = next((d for d in docs if d.get("doc_id") == doc_id), doc)

        result = {
            "doc_id": doc_id,
            "path": str(latest.get("path") or ""),
            "status": str(latest.get("status") or "uploaded"),
            "ingested": ingested,
            "active": do_activate,
            "source": source,
        }
        if ingest_skipped_reason:
            result["ingest_skipped_reason"] = ingest_skipped_reason
        await emit({"type": "kb_rulebook_sync_status", "status": "ready", "result": result})
        return result

    def _collection_target(self, doc_kind: str | None, *, explicit: str | None = None) -> str:
        s = self.settings_store.get()
        x = str(explicit or "").strip().lower()
        if x in {"game", "guidance", "default"}:
            if not s.knowledge.split_collections:
                return "default"
            if x == "default":
                return "game"
            return x
        if not s.knowledge.split_collections:
            return "default"
        dk = str(doc_kind or "").strip().lower()
        if dk in {"gm_advice", "guidance", "guide", "best_practices"}:
            return "guidance"
        return "game"

    async def begin_upload(
        self,
        *,
        filename: str,
        doc_id: str,
        ruleset: str | None,
        doc_kind: str | None,
        collection_target: str | None,
        total_bytes: int | None,
    ) -> tuple[str, str]:
        upload_id = uuid.uuid4().hex
        did = _slug_doc_id(doc_id)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        path = self.uploads_dir / f"{upload_id}.pdf"
        chosen_target = self._collection_target(doc_kind, explicit=collection_target)
        u = _Upload(
            upload_id=upload_id,
            filename=filename,
            doc_id=did,
            ruleset=ruleset,
            doc_kind=doc_kind,
            collection_target=chosen_target,
            path=path,
            total_bytes=total_bytes,
        )

        async with self._lock:
            self._uploads[upload_id] = u
            idx = _load_index(self.index_path)
            docs = list(idx.get("documents") or [])
            # Replace existing doc metadata for same doc_id (re-upload flow).
            docs = [d for d in docs if d.get("doc_id") != did]
            docs.append(
                {
                    "doc_id": did,
                    "filename": filename,
                    "ruleset": ruleset,
                    "doc_kind": doc_kind,
                    "collection_target": chosen_target,
                    "path": str(path),
                    "status": "uploading",
                    "created_at": time.time(),
                }
            )
            idx["documents"] = docs
            _atomic_write_json(self.index_path, idx)

        # Create/truncate file.
        await asyncio.to_thread(path.write_bytes, b"")
        return upload_id, did

    async def upload_chunk(self, *, upload_id: str, seq: int, data_b64: str) -> dict[str, Any]:
        async with self._lock:
            u = self._uploads.get(upload_id)
        if not u:
            raise RuntimeError("unknown upload_id")

        data = base64.b64decode(data_b64.encode("ascii"))
        await asyncio.to_thread(_append_bytes, u.path, data)
        u.received_bytes += len(data)
        return {"received_bytes": u.received_bytes, "total_bytes": u.total_bytes, "seq": seq}

    async def finish_upload(self, *, upload_id: str) -> dict[str, Any]:
        async with self._lock:
            u = self._uploads.pop(upload_id, None)
        if not u:
            raise RuntimeError("unknown upload_id")

        async with self._lock:
            idx = _load_index(self.index_path)
            docs = list(idx.get("documents") or [])
            for d in docs:
                if d.get("doc_id") == u.doc_id:
                    d["status"] = "uploaded"
                    d["received_bytes"] = u.received_bytes
                    if u.total_bytes is not None:
                        d["total_bytes"] = u.total_bytes
                    d["path"] = str(u.path)
                    d["collection_target"] = u.collection_target or self._collection_target(u.doc_kind)
            idx["documents"] = docs
            _atomic_write_json(self.index_path, idx)

        return {"doc_id": u.doc_id, "path": str(u.path), "received_bytes": u.received_bytes}

    async def ingest_doc(
        self,
        *,
        doc_id: str,
        progress_cb: ProgressCB | None = None,
        chunk_max_chars: int | None = None,
        chunk_overlap: int | None = None,
        ruleset: str | None = None,
        replace_existing: bool = True,
    ) -> None:
        if not self.qdrant:
            raise RuntimeError("Knowledge is not enabled (Qdrant not configured).")

        async def emit(payload: dict[str, Any]) -> None:
            if progress_cb:
                await progress_cb(payload)

        try:
            # Find doc on disk from index.
            docs = await self.list_documents()
            match = next((d for d in docs if d.get("doc_id") == doc_id), None)
            if not match:
                raise RuntimeError("Unknown doc_id. Upload a PDF first.")
            pdf_path = Path(str(match.get("path") or ""))
            if not pdf_path.exists():
                raise RuntimeError("PDF file not found on disk.")

            s = self.settings_store.get()
            max_chars = int(chunk_max_chars or s.knowledge.chunk_max_chars)
            overlap = int(chunk_overlap or s.knowledge.chunk_overlap)
            ruleset = ruleset if ruleset is not None else (match.get("ruleset") or None)
            doc_kind = match.get("doc_kind") or None
            collection_target = match.get("collection_target") or self._collection_target(str(doc_kind or ""))

            await emit({"type": "kb_ingest_status", "doc_id": doc_id, "status": "starting"})

            # Mark status ingesting.
            await self._set_doc_status(doc_id, status="ingesting", error=None)

            # Lazy import so `.[knowledge]` remains optional unless you ingest PDFs.
            from gm_engine.knowledge.ingest.pdf_ingest import ingest_pdf

            chunks = await asyncio.to_thread(
                ingest_pdf,
                pdf_path,
                doc_id=doc_id,
                ruleset=ruleset,
                doc_kind=str(doc_kind) if doc_kind is not None else None,
                chunk_max_chars=max_chars,
                chunk_overlap=overlap,
            )
            total = len(chunks)
            type_counts: dict[str, int] = {}
            for c in chunks:
                t = (c.tags or {}).get("type") if hasattr(c, "tags") else None
                t = str(t or "unknown")
                type_counts[t] = type_counts.get(t, 0) + 1
            await emit({"type": "kb_ingest_status", "doc_id": doc_id, "status": "chunked", "total": total})

            if replace_existing:
                await self.qdrant.delete_by_filter(filters={"doc_id": doc_id})

            # Embed + upsert.
            from qdrant_client.http import models as qmodels

            batch = 64
            done = 0
            for i in range(0, total, batch):
                part = chunks[i : i + batch]
                texts = [c.text for c in part]
                vectors = await self.qdrant.embedder.embed_texts(texts)
                points = []
                for c, v in zip(part, vectors, strict=True):
                    payload = {
                        "text": c.text,
                        "doc_id": c.tags.get("doc_id"),
                        "ruleset": c.tags.get("ruleset"),
                        "doc_kind": c.tags.get("doc_kind"),
                        "collection_target": collection_target,
                        "type": c.tags.get("type"),
                        "source_path": c.source_path,
                        "page": c.page,
                        "chunk_index": c.chunk_index,
                    }
                    points.append(qmodels.PointStruct(id=str(uuid.uuid4()), vector=v, payload=payload))
                await self.qdrant.upsert_points(points)

                done += len(part)
                await emit({"type": "kb_ingest_progress", "doc_id": doc_id, "done": done, "total": total})

            await self._set_doc_status(doc_id, status="ready", error=None, chunks=total, type_counts=type_counts)
            await emit({"type": "kb_ingest_status", "doc_id": doc_id, "status": "ready", "total": total})
        except Exception as e:
            await self._set_doc_status(doc_id, status="error", error=str(e))
            await emit({"type": "kb_ingest_status", "doc_id": doc_id, "status": "error", "error": str(e)})
            raise

    async def delete_doc(self, *, doc_id: str, delete_file: bool = False) -> None:
        if self.qdrant:
            await self.qdrant.delete_by_filter(filters={"doc_id": doc_id})

        async with self._lock:
            idx = _load_index(self.index_path)
            docs = list(idx.get("documents") or [])
            victim = next((d for d in docs if d.get("doc_id") == doc_id), None)
            docs = [d for d in docs if d.get("doc_id") != doc_id]
            idx["documents"] = docs
            _atomic_write_json(self.index_path, idx)

        if delete_file and victim and victim.get("path"):
            try:
                Path(str(victim["path"])).unlink(missing_ok=True)
            except Exception:
                pass

    async def _set_doc_status(
        self,
        doc_id: str,
        *,
        status: str,
        error: str | None,
        chunks: int | None = None,
        type_counts: dict[str, int] | None = None,
    ) -> None:
        async with self._lock:
            idx = _load_index(self.index_path)
            docs = list(idx.get("documents") or [])
            for d in docs:
                if d.get("doc_id") == doc_id:
                    d["status"] = status
                    if chunks is not None:
                        d["chunks"] = int(chunks)
                    if type_counts is not None:
                        d["type_counts"] = dict(type_counts)
                    if error:
                        d["error"] = error
                    else:
                        d.pop("error", None)
            idx["documents"] = docs
            _atomic_write_json(self.index_path, idx)


def _append_bytes(path: Path, data: bytes) -> None:
    with path.open("ab") as f:
        f.write(data)
