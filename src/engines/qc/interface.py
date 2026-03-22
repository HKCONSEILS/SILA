"""Interface abstraite pour le controle qualite.

Voir MASTERPLAN.md §5.3 — QC_Interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class QCResult:
    """Resultat du controle qualite. Voir MASTERPLAN.md §5.3."""

    utmos_score: float | None = None
    duration_ms: int = 0
    timing_delta_ms: int = 0
    flags: list[str] = field(default_factory=list)


class QCInterface(ABC):
    """Interface abstraite pour le QC. Voir MASTERPLAN.md §5.3."""

    @abstractmethod
    def check(self, audio_path: Path, reference_duration_ms: int) -> QCResult:
        """Verifie la qualite d'un segment audio.

        Args:
            audio_path: Chemin vers le fichier audio a verifier.
            reference_duration_ms: Duree de reference (timing_budget_ms).

        Returns:
            QCResult avec score, duree et flags.
        """
        ...
