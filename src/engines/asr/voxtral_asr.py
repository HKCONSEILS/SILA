"""Voxtral ASR stub — future ASR engine (Phase 3.1).

Placeholder for Voxtral Transcribe V2 integration. Not yet implemented.
"""

from __future__ import annotations
from pathlib import Path
from src.engines.asr.interface import ASRInterface, RawTranscript


class VoxtralASR(ASRInterface):
    """Voxtral Transcribe V2 stub. Not yet implemented."""

    def transcribe(self, audio_path: Path, language: str = "fr") -> RawTranscript:
        raise NotImplementedError("Voxtral ASR not yet implemented. Use --asr-engine whisperx.")
