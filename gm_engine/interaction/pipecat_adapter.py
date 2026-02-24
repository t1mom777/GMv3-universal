from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from gm_engine.rlm.controller import RLMController
from gm_engine.rlm.types import TurnContext


class StreamingTTS(Protocol):
    async def synthesize_stream(self, text: str, *, voice: str, locale: str) -> AsyncIterator[bytes]: ...


@dataclass
class PipecatAdapter:
    """Interaction layer boundary.

    This module is the ONLY place that should know about STT/TTS streaming.
    It must never call an LLM directly.
    """

    controller: RLMController
    tts: StreamingTTS

    async def on_final_transcript(self, ctx: TurnContext, *, voice: str = "default") -> AsyncIterator[bytes]:
        # Turn -> RLM -> narration plan
        plan = await self.controller.handle_turn(ctx)

        # Start streaming TTS immediately for the first chunk
        async for audio in self.tts.synthesize_stream(plan.immediate_text, voice=voice, locale=ctx.locale):
            yield audio

        # Followups may be generated immediately or in future iterations; stream them sequentially.
        for t in plan.followups:
            async for audio in self.tts.synthesize_stream(t, voice=voice, locale=ctx.locale):
                yield audio

        # Never block for background work
        await asyncio.sleep(0)
