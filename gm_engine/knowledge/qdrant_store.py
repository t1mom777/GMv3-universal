from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

try:  # Optional dependency (installed via `.[knowledge]`)
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels
except ModuleNotFoundError:  # pragma: no cover
    QdrantClient = Any  # type: ignore[misc,assignment]
    qmodels = Any  # type: ignore[assignment]

from gm_engine.knowledge.embeddings import Embedder
from gm_engine.knowledge.store import KnowledgeStore
from gm_engine.rlm.types import RetrievalSpec, TurnContext


def _filters_to_qdrant(filters: dict[str, Any] | None):
    if not filters:
        return None
    must = []
    for k, v in filters.items():
        if v is None:
            continue
        # Support "any of" matching via lists (e.g., active_doc_ids).
        if isinstance(v, (list, tuple, set)):
            vals = [vv for vv in v if vv is not None]
            if not vals:
                continue
            if hasattr(qmodels, "MatchAny"):
                match = qmodels.MatchAny(any=list(vals))
            else:  # pragma: no cover
                match = qmodels.MatchValue(value=list(vals)[0])
        else:
            match = qmodels.MatchValue(value=v)
        must.append(qmodels.FieldCondition(key=str(k), match=match))
    return qmodels.Filter(must=must)


@dataclass
class QdrantStore(KnowledgeStore):
    client: QdrantClient  # type: ignore[valid-type]
    embedder: Embedder
    collection: str = "gm_knowledge"

    _ready: bool = field(default=False, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _vector_size: int | None = field(default=None, init=False)

    async def ensure_collection(self) -> None:
        if self._ready:
            return
        if QdrantClient is Any:  # pragma: no cover
            raise RuntimeError("qdrant-client is not installed. Install with: pip install -e '.[knowledge]'")

        async with self._lock:
            if self._ready:
                return

            # Discover embedding dimension once (OpenAI embeddings are fixed per model).
            if self._vector_size is None:
                vec = (await self.embedder.embed_texts(["dimension_probe"]))[0]
                self._vector_size = len(vec)

            # Create collection if missing.
            try:
                await asyncio.to_thread(self.client.get_collection, self.collection)
            except Exception:
                await asyncio.to_thread(
                    self.client.create_collection,
                    collection_name=self.collection,
                    vectors_config=qmodels.VectorParams(size=self._vector_size, distance=qmodels.Distance.COSINE),
                )

            self._ready = True

    async def search(self, ctx: TurnContext, spec: RetrievalSpec) -> list[dict]:
        await self.ensure_collection()
        vec = (await self.embedder.embed_texts([spec.query]))[0]
        f = _filters_to_qdrant(spec.filters)

        try:
            res = await asyncio.to_thread(
                self.client.query_points,
                collection_name=self.collection,
                query=vec,
                limit=spec.top_k,
                query_filter=f,
                with_payload=True,
                with_vectors=False,
            )
        except Exception:
            # Common failures in dev:
            # - Qdrant not running
            # - collection not created yet
            return []

        out: list[dict] = []
        for p in res.points or []:
            payload = p.payload or {}
            out.append(
                {
                    "id": p.id,
                    "score": p.score,
                    "text": payload.get("text", ""),
                    "meta": payload,
                }
            )
        return out

    async def upsert_points(self, points: list[Any]) -> None:
        await self.ensure_collection()
        await asyncio.to_thread(self.client.upsert, collection_name=self.collection, points=points)

    async def delete_by_filter(self, *, filters: dict[str, Any]) -> None:
        await self.ensure_collection()
        f = _filters_to_qdrant(filters)
        if not f:
            return
        await asyncio.to_thread(
            self.client.delete,
            collection_name=self.collection,
            points_selector=qmodels.FilterSelector(filter=f),
        )
