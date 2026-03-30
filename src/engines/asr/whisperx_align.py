"""WhisperX Alignment — word-level timestamps (Phase 3.2).

Decomposed from whisperx_engine.py. Uses wav2vec2 for alignment.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.engines.asr.interface import AlignInterface, RawTranscript, AlignedTranscript

logger = logging.getLogger(__name__)


class WhisperXAlign(AlignInterface):
    """WhisperX word-level alignment (wav2vec2)."""

    def __init__(self, device: str = "cuda"):
        self.device = device

    def align(self, transcript: RawTranscript, audio_path: Path) -> AlignedTranscript:
        import whisperx

        logger.info("Aligning transcription (%d segments)...", len(transcript.segments))
        audio = transcript.audio
        if audio is None:
            audio = whisperx.load_audio(str(audio_path))

        model_a, metadata = whisperx.load_align_model(language_code=transcript.language, device=self.device)
        result = whisperx.align(
            transcript.segments, model_a, metadata, audio, self.device,
            return_char_alignments=False,
        )
        del model_a

        logger.info("Alignment done: %d segments", len(result.get("segments", [])))
        return AlignedTranscript(segments=result["segments"], language=transcript.language, audio=audio)
