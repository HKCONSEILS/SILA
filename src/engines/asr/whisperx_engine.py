"""WhisperX ASR engine — Phase 3.

Voir MASTERPLAN.md §3.1 — WhisperX (faster-whisper backend).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from src.engines.asr.interface import ASRInterface, TranscriptResult

logger = logging.getLogger(__name__)


class WhisperXEngine(ASRInterface):
    """WhisperX ASR avec word-level timestamps."""

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "float16",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return
        import whisperx
        logger.info("Loading WhisperX model %s on %s (%s)...", self.model_size, self.device, self.compute_type)
        self._model = whisperx.load_model(
            self.model_size,
            self.device,
            compute_type=self.compute_type,
        )
        logger.info("WhisperX model loaded.")

    def transcribe(self, audio_path: Path, language: str = "fr") -> TranscriptResult:
        """Transcrit un fichier audio avec WhisperX."""
        import whisperx

        self._load_model()

        logger.info("Transcribing %s (lang=%s)...", audio_path, language)
        audio = whisperx.load_audio(str(audio_path))
        result = self._model.transcribe(audio, batch_size=16, language=language)

        # Alignement word-level
        logger.info("Aligning transcription...")
        model_a, metadata = whisperx.load_align_model(language_code=language, device=self.device)
        result = whisperx.align(
            result["segments"],
            model_a,
            metadata,
            audio,
            self.device,
            return_char_alignments=False,
        )
        del model_a

        # Convertir en format interne
        words = []
        for seg in result.get("segments", []):
            for w in seg.get("words", []):
                if "start" not in w or "end" not in w:
                    continue
                words.append({
                    "text": w["word"].strip(),
                    "start_ms": int(w["start"] * 1000),
                    "end_ms": int(w["end"] * 1000),
                    "confidence": w.get("score", 0.0),
                })

        logger.info("Transcription done: %d words", len(words))
        return TranscriptResult(words=words, language=language)

    def unload(self):
        """Libere la VRAM."""
        import gc, torch
        self._model = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("WhisperX model unloaded.")
