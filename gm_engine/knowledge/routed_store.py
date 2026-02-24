from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from gm_engine.knowledge.qdrant_store import QdrantStore
from gm_engine.knowledge.store import KnowledgeStore
from gm_engine.rlm.types import RetrievalSpec, TurnContext


def _as_doc_kind_set(v: Any) -> set[str]:
    if isinstance(v, (list, tuple, set)):
        return {str(x or "").strip().lower() for x in v if str(x or "").strip()}
    s = str(v or "").strip().lower()
    return {s} if s else set()


@dataclass
class RoutedQdrantStore(KnowledgeStore):
    """Route knowledge vectors between game and guidance collections."""

    game: QdrantStore
    guidance: QdrantStore
    guidance_doc_kinds: set[str] = field(
        default_factory=lambda: {"gm_advice", "guidance", "guide", "best_practices"}
    )

    @property
    def embedder(self):
        return self.game.embedder

    def _route_from_filters(self, filters: dict[str, Any] | None) -> str:
        if not filters:
            return "both"
        target = str(filters.get("collection_target") or "").strip().lower()
        if target in {"game", "guidance"}:
            return target

        doc_kinds = _as_doc_kind_set(filters.get("doc_kind"))
        if doc_kinds:
            if doc_kinds.issubset(self.guidance_doc_kinds):
                return "guidance"
            if doc_kinds.isdisjoint(self.guidance_doc_kinds):
                return "game"
        return "both"

    async def search(self, ctx: TurnContext, spec: RetrievalSpec) -> list[dict]:
        route = self._route_from_filters(spec.filters)
        if route == "game":
            return await self.game.search(ctx, spec)
        if route == "guidance":
            return await self.guidance.search(ctx, spec)

        game_task = asyncio.create_task(self.game.search(ctx, spec))
        guidance_task = asyncio.create_task(self.guidance.search(ctx, spec))
        game_res, guidance_res = await asyncio.gather(game_task, guidance_task)
        merged = list(game_res or []) + list(guidance_res or [])
        merged.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)

        # Deduplicate near-identical hits across collections.
        out: list[dict] = []
        seen: set[str] = set()
        for r in merged:
            meta = r.get("meta") if isinstance(r, dict) else None
            meta = meta if isinstance(meta, dict) else {}
            key = "|".join(
                [
                    str(meta.get("doc_id") or ""),
                    str(meta.get("page") or ""),
                    str(meta.get("chunk_index") or ""),
                    str(r.get("text") or "")[:96],
                ]
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
            if len(out) >= int(spec.top_k):
                break
        return out

    async def upsert_points(self, points: list[Any]) -> None:
        if not points:
            return
        game_points: list[Any] = []
        guidance_points: list[Any] = []
        for p in points:
            payload = getattr(p, "payload", None)
            payload = payload if isinstance(payload, dict) else {}
            target = str(payload.get("collection_target") or "").strip().lower()
            doc_kind = str(payload.get("doc_kind") or "").strip().lower()
            if target == "guidance" or doc_kind in self.guidance_doc_kinds:
                guidance_points.append(p)
            else:
                game_points.append(p)

        if game_points:
            await self.game.upsert_points(game_points)
        if guidance_points:
            await self.guidance.upsert_points(guidance_points)

    async def delete_by_filter(self, *, filters: dict[str, Any]) -> None:
        route = self._route_from_filters(filters)
        if route == "game":
            await self.game.delete_by_filter(filters=filters)
            return
        if route == "guidance":
            await self.guidance.delete_by_filter(filters=filters)
            return
        await asyncio.gather(
            self.game.delete_by_filter(filters=filters),
            self.guidance.delete_by_filter(filters=filters),
        )

