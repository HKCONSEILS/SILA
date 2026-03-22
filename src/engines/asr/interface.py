"""Interface abstraite pour les moteurs ASR.

Voir MASTERPLAN.md §5.3 — ASR_Interface.
Principe P12 : separer interface de tache et moteur concret.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TranscriptResult:
    """Resultat de transcription. Voir MASTERPLAN.md §5.3."""

    words: list[dict]
    speakers: list[str] = field(default_factory=list)
    language: str = ""


class ASRInterface(ABC):
    """Interface abstraite pour les moteurs ASR. Voir MASTERPLAN.md §5.3."""

    @abstractmethod
    def transcribe(self, audio_path: Path) -> TranscriptResult:
        """Transcrit un fichier audio WAV 16kHz mono.

        Args:
            audio_path: Chemin vers le WAV 16kHz mono (downsample pour inference).

        Returns:
            TranscriptResult avec mots, locuteurs et langue detectee.
        """
        ...
