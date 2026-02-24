from __future__ import annotations

import time
from dataclasses import dataclass, field

try:
    from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
except ModuleNotFoundError:  # pragma: no cover
    FrameProcessor = object  # type: ignore[assignment,misc]
    FrameDirection = None  # type: ignore[assignment]
    BotStartedSpeakingFrame = object  # type: ignore[assignment]
    BotStoppedSpeakingFrame = object  # type: ignore[assignment]


@dataclass
class BotSpeakingState:
    gm_speaking: bool = False
    last_started_at: float = 0.0
    last_stopped_at: float = 0.0


@dataclass
class BotSpeakingStateProcessor(FrameProcessor):  # type: ignore[misc]
    """Track bot speaking state from downstream speaking frames."""

    state: BotSpeakingState = field(default_factory=BotSpeakingState)

    def __post_init__(self) -> None:
        if FrameDirection is None:  # pragma: no cover
            raise RuntimeError(
                "Pipecat is not installed. Install with a Python <3.14 env: pip install -e '.[voice]'"
            )
        super().__init__(name="BotSpeakingStateProcessor")

    async def process_frame(self, frame, direction):  # Frame, FrameDirection
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM:
            now = time.perf_counter()
            if isinstance(frame, BotStartedSpeakingFrame):
                self.state.gm_speaking = True
                self.state.last_started_at = now
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self.state.gm_speaking = False
                self.state.last_stopped_at = now

        await self.push_frame(frame, direction)
