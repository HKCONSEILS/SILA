"""WhisperX ASR — transcription only (Phase 3.1).

Decomposed from whisperx_engine.py. Handles only Whisper transcription.
"""

from __future__ import annotations

import gc
import logging
from pathlib import Path

from src.engines.asr.interface import ASRInterface, RawTranscript

logger = logging.getLogger(__name__)


class WhisperXASR(ASRInterface):
    """WhisperX transcription (faster-whisper backend)."""

    def __init__(self, model_size: str = "large-v3", device: str = "cuda", compute_type: str = "float16"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load(self):
        if self._model is not None:
            return
        import whisperx
        logger.info("Loading WhisperX model %s on %s (%s)...", self.model_size, self.device, self.compute_type)
        self._model = whisperx.load_model(self.model_size, self.device, compute_type=self.compute_type)
        logger.info("WhisperX ASR model loaded.")

    def transcribe(self, audio_path: Path, language: str = "fr") -> RawTranscript:
        import whisperx
        self._load()
        logger.info("Transcribing %s (lang=%s)...", audio_path, language)
        audio = whisperx.load_audio(str(audio_path))
        result = self._model.transcribe(audio, batch_size=16, language=language)
        logger.info("ASR done: %d segments", len(result.get("segments", [])))
        return RawTranscript(segments=result["segments"], language=language, audio=audio)

    def unload(self):
        import torch
        self._model = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("WhisperX ASR unloaded.")
