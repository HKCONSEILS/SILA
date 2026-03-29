"""Wrapper pyrubberband pour le time-stretching.

Voir MASTERPLAN.md v1.3.0 — qualite-first.
Stretch max 1.10x (acceleration), slow-down min 0.85x (ralentissement).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_STRETCH_RATIO = 1.10   # acceleration max (qualite-first)
MIN_SLOWDOWN_RATIO = 0.85  # ralentissement max


def time_stretch(
    input_path: Path,
    output_path: Path,
    ratio: float,
    sample_rate: int = 48000,
) -> Path:
    """Applique un time-stretch (acceleration ou ralentissement).

    Args:
        input_path: Fichier WAV source.
        output_path: Fichier WAV de sortie.
        ratio: Facteur (> 1.0 = accelerer, < 1.0 = ralentir).

    Returns:
        Chemin vers le fichier stretche.

    Raises:
        ValueError: Si le ratio depasse les limites.
    """
    if ratio > MAX_STRETCH_RATIO:
        raise ValueError(
            f"Stretch ratio {ratio:.3f} exceeds max {MAX_STRETCH_RATIO}."
        )
    if ratio < MIN_SLOWDOWN_RATIO:
        raise ValueError(
            f"Slowdown ratio {ratio:.3f} below min {MIN_SLOWDOWN_RATIO}."
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
