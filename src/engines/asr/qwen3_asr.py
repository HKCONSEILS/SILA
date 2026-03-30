"""Qwen3-ASR stub — future ASR engine (Phase 3.1).

Placeholder for Qwen3-ASR integration. Not yet implemented.
"""

from __future__ import annotations
from pathlib import Path
from src.engines.asr.interface import ASRInterface, RawTranscript


class Qwen3ASR(ASRInterface):
    """Qwen3-ASR stub. Not yet implemented."""

    def transcribe(self, audio_path: Path, language: str = "fr") -> RawTranscript:
        raise NotImplementedError("Qwen3-ASR not yet implemented. Use --asr-engine whisperx.")
