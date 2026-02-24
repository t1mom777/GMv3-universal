from __future__ import annotations

import time
from typing import Any

from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601


class DeepgramNovaDiarizationSTTService(DeepgramSTTService):
    """Deepgram STT with speaker diarization mapped into TranscriptionFrame.user_id."""

    def _pick_speaker_user_id(self, result: Any) -> str:
        now = time.monotonic()
        try:
            alts = getattr(getattr(result, "channel", None), "alternatives", None) or []
            words = getattr(alts[0], "words", None) or [] if alts else []

            counts: dict[str, int] = {}
            for w in words:
                speaker = getattr(w, "speaker", None)
                if speaker is None and isinstance(w, dict):
                    speaker = w.get("speaker")
                if speaker is None:
                    continue
                sid = f"dg_spk_{speaker}"
                counts[sid] = counts.get(sid, 0) + 1

            if counts:
                # Pick the dominant diarized speaker label in this segment.
                winner = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[0][0]
                setattr(self, "_last_dg_speaker_user_id", winner)
                setattr(self, "_last_dg_speaker_ts", now)
                return winner
        except Exception:
            pass
        # Short utterances can occasionally omit per-word speaker labels.
        # Reuse very recent diarization to avoid falling back to UI-selected player id.
        recent = str(getattr(self, "_last_dg_speaker_user_id", "") or "").strip()
        recent_ts = float(getattr(self, "_last_dg_speaker_ts", 0.0) or 0.0)
        if recent and (now - recent_ts) <= 4.0:
            return recent
        return ""

    async def _on_message(self, *args, **kwargs):
        result = kwargs["result"]
        if len(result.channel.alternatives) == 0:
            return
        is_final = result.is_final
        transcript = result.channel.alternatives[0].transcript
        language = None
        if result.channel.alternatives[0].languages:
            language = result.channel.alternatives[0].languages[0]
            language = Language(language)
        if len(transcript) > 0:
            speaker_user_id = self._pick_speaker_user_id(result)
            if is_final:
                from_finalize = getattr(result, "from_finalize", False)
                if from_finalize:
                    self.confirm_finalize()
                await self.push_frame(
                    TranscriptionFrame(
                        transcript,
                        speaker_user_id,
                        time_now_iso8601(),
                        language,
                        result=result,
                    )
                )
                await self._handle_transcription(transcript, is_final, language)
                await self.stop_processing_metrics()
            else:
                await self.push_frame(
                    InterimTranscriptionFrame(
                        transcript,
                        speaker_user_id,
                        time_now_iso8601(),
                        language,
                        result=result,
                    )
                )
