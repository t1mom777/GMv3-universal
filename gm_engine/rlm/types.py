from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ChunkType = Literal[
    "rules",
    "lore",
    "examples",
    "tables",
    "characters",
    "locations",
    "quests",
    "factions",
    "items",
    "monsters",
    "gm_advice",
    "story",
    "unknown",
]


@dataclass(frozen=True)
class Budget:
    max_depth: int = 2
    max_llm_calls_per_turn: int = 3
    max_qdrant_queries_per_turn: int = 2
    max_sql_reads_per_turn: int = 20
    max_sql_writes_per_turn: int = 20


@dataclass(frozen=True)
class TurnContext:
    campaign_id: str
    session_id: str
    turn_id: str
    player_id: str
    transcript_text: str
    locale: str = "en-US"


@dataclass(frozen=True)
class RetrievalSpec:
    query: str
    top_k: int = 5
    filters: dict[str, Any] | None = None


@dataclass(frozen=True)
class StateReadSpec:
    kind: str
    params: dict[str, Any]


@dataclass(frozen=True)
class StateWriteSpec:
    kind: str
    params: dict[str, Any]


@dataclass(frozen=True)
class NarrationPlan:
    # First chunk must be produced fast to start streaming TTS.
    immediate_text: str
    # Optional follow-up chunks (can be produced asynchronously)
    followups: list[str]
    # State writes to commit (transaction)
    writes: list[StateWriteSpec]
    # Delayed events to schedule (async)
    delayed_events: list[dict[str, Any]]
    # Optional diagnostics surfaced to client/UI.
    debug: dict[str, Any] | None = None
