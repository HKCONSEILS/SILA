"""Interface abstraite pour les moteurs TTS.

Voir MASTERPLAN.md §5.3 — TTS_Interface.
Principe P12 : separer interface de tache et moteur concret.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TTSResult:
    """Resultat de synthese vocale. Voir MASTERPLAN.md §5.3."""

    audio_path: Path
    duration_ms: int
    sample_rate: int = 48000


class TTSInterface(ABC):
    """Interface abstraite pour le TTS. Voir MASTERPLAN.md §5.3."""

    @abstractmethod
    def synthesize(
        self,
        text: str,
        voice_profile: Path,
        target_lang: str,
        target_duration_ms: int,
        seed: int = 42,
    ) -> TTSResult:
        """Genere l'audio TTS pour un segment.

        Args:
            text: Texte a synthetiser.
            voice_profile: Chemin vers le profil voix (reference audio).
            target_lang: Code ISO 639-1 de la langue cible.
            target_duration_ms: Duree cible en ms (timing_budget_ms).
            seed: Seed pour reproductibilite (principe P11).

        Returns:
            TTSResult avec chemin audio, duree et sample rate.
        """
        ...
