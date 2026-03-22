"""Wrapper pyrubberband pour le time-stretching.

Voir MASTERPLAN.md §3.1 — pyrubberband, max ratio 1.25x.
Principe P2 : cascade de duree — time-stretch en dernier recours.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_STRETCH_RATIO = 1.25


def time_stretch(
    input_path: Path,
    output_path: Path,
    ratio: float,
    sample_rate: int = 48000,
) -> Path:
    """Applique un time-stretch a un fichier audio.

    Voir MASTERPLAN.md §6.1 Phase 8.3 — time-stretch <= 1.25x.
    Le suffixe _adj est ajoute par convention (§11.3).

    Args:
        input_path: Fichier WAV source.
        output_path: Fichier WAV de sortie (suffixe _adj).
        ratio: Facteur de stretch (> 1.0 = ralentir, < 1.0 = accelerer).
        sample_rate: Frequence d'echantillonnage.

    Returns:
        Chemin vers le fichier stretche.

    Raises:
        ValueError: Si le ratio depasse MAX_STRETCH_RATIO.
    """
    if ratio > MAX_STRETCH_RATIO:
        raise ValueError(
            f"Stretch ratio {ratio:.3f} exceeds max {MAX_STRETCH_RATIO}. "
            "Voir MASTERPLAN.md §6.1 Phase 8.4 — review_required."
        )

    import numpy as np
    import pyrubberband as pyrb
    import soundfile as sf

    audio, sr = sf.read(str(input_path))
    stretched = pyrb.time_stretch(audio, sr, ratio)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), stretched, sr)
    logger.info("Time-stretched %s -> %s (ratio %.3f)", input_path.name, output_path.name, ratio)
    return output_path
