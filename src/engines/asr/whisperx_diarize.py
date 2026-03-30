"""WhisperX Diarization — speaker identification (Phase 3.3).

Decomposed from whisperx_engine.py. Uses pyannote for speaker diarization.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from src.engines.asr.interface import DiarizeInterface, AlignedTranscript, DiarizeResult

logger = logging.getLogger(__name__)


class WhisperXDiarize(DiarizeInterface):
    """WhisperX diarization via pyannote."""

    def __init__(self, device: str = "cuda"):
        self.device = device

    def _get_hf_token(self) -> str | None:
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        if not token:
            token_path = Path.home() / ".huggingface" / "token"
            if token_path.exists():
                token = token_path.read_text().strip()
        return token

    def diarize(self, audio_path: Path, aligned: AlignedTranscript | None = None) -> DiarizeResult:
        import whisperx

        hf_token = self._get_hf_token()
        if not hf_token:
            logger.warning("No HuggingFace token. Diarization unavailable. Falling back to single speaker.")
            return DiarizeResult(speakers=["spk_0"])

        try:
            logger.info("Loading pyannote diarization pipeline...")
            from whisperx.diarize import DiarizationPipeline, assign_word_speakers
            diarize_model = DiarizationPipeline(token=hf_token, device=self.device)

            audio = aligned.audio if aligned else whisperx.load_audio(str(audio_path))
            logger.info("Running diarization...")
            diarize_segments = diarize_model(audio)

            # Assign speakers to aligned segments if available
            if aligned and aligned.segments:
                result = assign_word_speakers(diarize_segments, {"segments": aligned.segments})
                aligned.segments = result.get("segments", aligned.segments)

            # Collect speakers
            speakers = set()
            if aligned:
                for seg in aligned.segments:
                    if "speaker" in seg:
                        speakers.add(seg["speaker"])
                    for w in seg.get("words", []):
                        if "speaker" in w:
                            speakers.add(w["speaker"])

            speakers = sorted(speakers) if speakers else ["spk_0"]
            logger.info("Diarization done: %d speakers: %s", len(speakers), speakers)
            return DiarizeResult(segments=diarize_segments, speakers=speakers)

        except Exception as exc:
            logger.warning("Diarization failed: %s — falling back to single speaker", exc)
            return DiarizeResult(speakers=["spk_0"])
