"""Interfaces abstraites pour ASR, Alignement et Diarisation.

Voir MASTERPLAN.md §5.3 — P12 : separer interface de tache et moteur concret.
V2: Decomposition en 3 interfaces independantes (ASR + Align + Diarize).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TranscriptResult:
    """Resultat de transcription. Voir MASTERPLAN.md §5.3."""
    words: list[dict]
    speakers: list[str] = field(default_factory=list)
    language: str = ""


@dataclass
class RawTranscript:
    """Raw ASR output (segments, no word-level timestamps yet)."""
    segments: list[dict[str, Any]]
    language: str = ""
    audio: Any = None  # Loaded audio for alignment


@dataclass
class AlignedTranscript:
    """Aligned transcript with word-level timestamps."""
    segments: list[dict[str, Any]]
    language: str = ""
    audio: Any = None


@dataclass
class DiarizeResult:
    """Diarization output: speaker segments."""
    segments: Any = None  # pyannote-style segments
    speakers: list[str] = field(default_factory=list)


class ASRInterface(ABC):
    """Interface abstraite pour la transcription (Phase 3.1)."""

    @abstractmethod
    def transcribe(self, audio_path: Path, language: str = "fr") -> RawTranscript:
        """Transcrit un fichier audio. Retourne les segments bruts."""
        ...

    def unload(self):
        pass


class AlignInterface(ABC):
    """Interface abstraite pour l'alignement mot-a-mot (Phase 3.2)."""

    @abstractmethod
    def align(self, transcript: RawTranscript, audio_path: Path) -> AlignedTranscript:
        """Aligne les segments sur l'audio pour obtenir des timestamps mot-a-mot."""
        ...

    def unload(self):
        pass


class DiarizeInterface(ABC):
    """Interface abstraite pour la diarisation (Phase 3.3)."""

    @abstractmethod
    def diarize(self, audio_path: Path, aligned: AlignedTranscript | None = None) -> DiarizeResult:
        """Identifie les locuteurs dans l'audio."""
        ...

    def unload(self):
        pass


# Legacy compatibility: keep TranscriptResult for existing code
__all__ = [
    "TranscriptResult", "RawTranscript", "AlignedTranscript", "DiarizeResult",
    "ASRInterface", "AlignInterface", "DiarizeInterface",
]
