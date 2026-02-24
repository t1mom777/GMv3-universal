from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any

from gm_engine.interaction.control_frames import GMClientMessageFrame

try:
    from pipecat.serializers.base_serializer import FrameSerializer
except ModuleNotFoundError:  # pragma: no cover
    FrameSerializer = object  # type: ignore[assignment,misc]


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


@dataclass
class SimpleJSONFrameSerializer(FrameSerializer):  # type: ignore[misc]
    """A tiny JSON serializer for Pipecat websocket transport + browser client.

    Protocol (JSON messages):
      - client -> server:
        - {"type":"audio_in","sample_rate":16000,"num_channels":1,"audio":"<base64 pcm16>"}
        - {"type":"vad","state":"start"|"stop"}
      - server -> client:
        - {"type":"audio_out","sample_rate":24000,"num_channels":1,"audio":"<base64 pcm16>"}
        - {"type":"transcript","text":"...","finalized":true}
        - {"type":"text","text":"..."}
        - {"type":"error","error":"..."}
        - {"type":"start","audio_in_sample_rate":16000,"audio_out_sample_rate":24000}
    """

    def __post_init__(self) -> None:
        # BaseObject is not a dataclass; ensure it gets initialized.
        try:
            super().__init__(name="SimpleJSONFrameSerializer")
        except TypeError:
            # In non-voice environments FrameSerializer is aliased to `object`.
            return

    async def setup(self, frame) -> None:  # StartFrame
        # No-op; we serialize StartFrame for client convenience.
        return None

    async def serialize(self, frame) -> str | bytes | None:
        try:
            from pipecat.frames.frames import (  # type: ignore
                EndFrame,
                ErrorFrame,
                OutputAudioRawFrame,
                OutputTransportMessageFrame,
                OutputTransportMessageUrgentFrame,
                StartFrame,
                TextFrame,
                TranscriptionFrame,
            )
        except ModuleNotFoundError:  # pragma: no cover
            return None

        msg: dict[str, Any] | None = None

        if isinstance(frame, StartFrame):
            msg = {
                "type": "start",
                "audio_in_sample_rate": frame.audio_in_sample_rate,
                "audio_out_sample_rate": frame.audio_out_sample_rate,
            }
        elif isinstance(frame, (OutputTransportMessageUrgentFrame, OutputTransportMessageFrame)):
            # Application-level messages for the browser UI.
            if isinstance(frame.message, dict):
                msg = frame.message
            else:
                msg = {"type": "message", "message": frame.message}
        elif isinstance(frame, OutputAudioRawFrame):
            msg = {
                "type": "audio_out",
                "sample_rate": frame.sample_rate,
                "num_channels": frame.num_channels,
                "audio": _b64e(frame.audio),
            }
        elif isinstance(frame, TranscriptionFrame):
            msg = {
                "type": "transcript",
                "text": frame.text,
                "finalized": bool(frame.finalized),
                "timestamp": frame.timestamp,
            }
        elif isinstance(frame, TextFrame):
            # Avoid duplicating transcript events as "text".
            if not isinstance(frame, TranscriptionFrame):
                msg = {"type": "text", "text": frame.text}
        elif isinstance(frame, ErrorFrame):
            msg = {"type": "error", "error": str(getattr(frame, "error", "") or "")}
        elif isinstance(frame, EndFrame):
            msg = {"type": "end"}

        if not msg:
            return None
        return json.dumps(msg, ensure_ascii=True)

    async def deserialize(self, data: str | bytes):
        try:
            from pipecat.frames.frames import (  # type: ignore
                InputAudioRawFrame,
                TranscriptionFrame,
                VADUserStartedSpeakingFrame,
                VADUserStoppedSpeakingFrame,
            )
        except ModuleNotFoundError as e:  # pragma: no cover
            raise RuntimeError(
                "Pipecat is not installed. Install a Python <3.14 environment and then: pip install -e '.[voice]'"
            ) from e

        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")

        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return None

        if not isinstance(obj, dict):
            return None

        t = obj.get("type")

        if t == "audio_in":
            audio = _b64d(str(obj.get("audio", "")))
            sample_rate = int(obj.get("sample_rate", 16000))
            num_channels = int(obj.get("num_channels", 1))
            frame = InputAudioRawFrame(audio=audio, sample_rate=sample_rate, num_channels=num_channels)
            user_id = str(obj.get("user_id") or "").strip()
            if user_id:
                # STTService checks frame.user_id when present.
                setattr(frame, "user_id", user_id)
            return frame

        if t == "vad":
            raw_vp = obj.get("voiceprint")
            vp: dict[str, float] | None = None
            if isinstance(raw_vp, dict):
                try:
                    pitch = float(raw_vp.get("pitch_hz", 0) or 0)
                    zcr = float(raw_vp.get("zcr", 0) or 0)
                    if (pitch == 0 or (50.0 <= pitch <= 450.0)) and (0.0 <= zcr <= 1.0):
                        vp = {"pitch_hz": pitch, "zcr": zcr}
                except Exception:
                    vp = None
            state = obj.get("state")
            if state == "start":
                frame = VADUserStartedSpeakingFrame()
                if "gm_speaking" in obj:
                    try:
                        setattr(frame, "gm_speaking", bool(obj.get("gm_speaking")))
                    except Exception:
                        pass
                if vp is not None:
                    try:
                        setattr(frame, "voiceprint", vp)
                    except Exception:
                        pass
                return frame
            if state == "stop":
                frame = VADUserStoppedSpeakingFrame()
                if "gm_speaking" in obj:
                    try:
                        setattr(frame, "gm_speaking", bool(obj.get("gm_speaking")))
                    except Exception:
                        pass
                if vp is not None:
                    try:
                        setattr(frame, "voiceprint", vp)
                    except Exception:
                        pass
                return frame
            return None

        if t == "manual_transcript":
            text = str(obj.get("text") or "").strip()
            if not text:
                return None
            user_id = str(obj.get("user_id") or "player")
            timestamp = str(obj.get("timestamp") or f"{time.time():.3f}")
            finalized = bool(obj.get("finalized", True))
            return TranscriptionFrame(text=text, user_id=user_id, timestamp=timestamp, finalized=finalized)

        # Any other JSON message is treated as an app-level control message.
        return GMClientMessageFrame(message=obj)
