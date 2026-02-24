from __future__ import annotations

from dataclasses import dataclass

from gm_engine.knowledge.store import KnowledgeStore
from gm_engine.rlm.types import RetrievalSpec, TurnContext


@dataclass(frozen=True)
class NullKnowledgeStore(KnowledgeStore):
    async def search(self, ctx: TurnContext, spec: RetrievalSpec) -> list[dict]:
        return []

