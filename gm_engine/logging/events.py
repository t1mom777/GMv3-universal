from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from gm_engine.rlm.types import NarrationPlan, TurnContext


@dataclass
class EventLogger:
    path: Path

    def _write(self, ctx: TurnContext, kind: str, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.time(),
            "kind": kind,
            "campaign_id": ctx.campaign_id,
            "session_id": ctx.session_id,
            "turn_id": ctx.turn_id,
            "player_id": ctx.player_id,
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def event(self, ctx: TurnContext, kind: str, payload: dict) -> None:
        self._write(ctx, kind, payload)

    def error(self, ctx: TurnContext, kind: str, payload: dict) -> None:
        self._write(ctx, f"error:{kind}", payload)

    def turn_started(self, ctx: TurnContext) -> None:
        self._write(ctx, "turn_started", {"transcript": ctx.transcript_text})

    def turn_finished(self, ctx: TurnContext, *, latency_ms: int) -> None:
        self._write(ctx, "turn_finished", {"latency_ms": latency_ms})

    def append_narration(self, ctx: TurnContext, plan: NarrationPlan) -> None:
        self._write(
            ctx,
            "narration",
            {"immediate": plan.immediate_text, "followups": plan.followups},
        )
