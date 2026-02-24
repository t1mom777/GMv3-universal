from __future__ import annotations

from typing import Protocol

from gm_engine.rlm.types import RetrievalSpec, TurnContext


class KnowledgeStore(Protocol):
    async def search(self, ctx: TurnContext, spec: RetrievalSpec) -> list[dict]: ...

