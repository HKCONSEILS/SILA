"""Interface abstraite pour la separation vocale.

Voir MASTERPLAN.md §3.1 — Demucs v4.
V1 : skippee (ADR-005). Implementee en V2.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SeparationResult:
    """Resultat de separation vocale."""

    voice_path: Path
    music_path: Path
    sfx_path: Path


class SeparationInterface(ABC):
    """Interface abstraite pour la separation vocale."""

    @abstractmethod
    def separate(self, audio_path: Path, output_dir: Path) -> SeparationResult:
        ...
