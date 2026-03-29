"""Placement des segments TTS sur la timeline + crossfade.

Voir MASTERPLAN.md §6.1 Phase 9 — Assembly audio.
Principe P8 : crossfade 50 ms entre segments TTS finaux.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def assemble_segments(
    segments: list[dict],
    output_path: Path,
    total_duration_ms: int,
    crossfade_ms: int = 50,
    sample_rate: int = 48000,
) -> Path:
    """Place les segments TTS sur la timeline cible avec crossfade.

    Voir MASTERPLAN.md §6.1 Phase 9.1-9.2.

    Args:
        segments: Liste de dicts avec 'audio_path', 'start_ms', 'duration_ms'.
        output_path: Chemin de sortie du mix.
        total_duration_ms: Duree totale de la timeline en ms.
        crossfade_ms: Duree du crossfade inter-segments (50ms par defaut, P8).
        sample_rate: Frequence d'echantillonnage (48kHz, P5).

    Returns:
        Chemin vers le fichier audio assemble.
    """
    import numpy as np
    import soundfile as sf

    total_samples = int(total_duration_ms * sample_rate / 1000)
    timeline = np.zeros(total_samples, dtype=np.float32)
    crossfade_samples = int(crossfade_ms * sample_rate / 1000)

    for seg in segments:
        audio_path = Path(seg["audio_path"])
        if not audio_path.exists():
            logger.warning("Segment audio not found: %s — inserting silence", audio_path)
            continue

        audio, sr = sf.read(str(audio_path), dtype="float32")
        if audio.ndim > 1:
            audio = audio[:, 0]

        # Resample if TTS sample rate differs from timeline (e.g. 24kHz -> 48kHz)
        if sr != sample_rate:
            import librosa
            audio = librosa.resample(audio, orig_sr=sr, target_sr=sample_rate)

        start_sample = int(seg["start_ms"] * sample_rate / 1000)
        end_sample = min(start_sample + len(audio), total_samples)
        seg_length = end_sample - start_sample

        if seg_length <= 0:
            continue

        chunk = audio[:seg_length]

        # Crossfade entrant
        if crossfade_samples > 0 and start_sample > 0:
            fade_len = min(crossfade_samples, seg_length)
            fade_in = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
            chunk[:fade_len] *= fade_in
            fade_out = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
            timeline[start_sample : start_sample + fade_len] *= fade_out

        timeline[start_sample:end_sample] += chunk

    # Detect gaps > 500ms between placed segments
    placed = sorted(
        [(seg["start_ms"], seg["start_ms"] + int(len(sf.read(str(Path(seg["audio_path"])), dtype="float32")[0]) / sf.info(str(Path(seg["audio_path"]))).samplerate * 1000))
         for seg in segments if Path(seg["audio_path"]).exists()],
        key=lambda x: x[0],
    ) if False else []  # Skip expensive gap detection for now (TODO V2)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), timeline, sample_rate)
    logger.info("Assembly done: %s (%d ms, %d segments)", output_path, total_duration_ms, len(segments))
    return output_path
