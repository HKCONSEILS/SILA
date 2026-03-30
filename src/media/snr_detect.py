"""Background audio detection for automatic Demucs activation.

Analyses audio spectral characteristics to detect background music/SFX.
Key metric: energy in music-typical frequency bands during quiet vocal moments.
Speech-only audio has very low background energy during pauses; music persists.

Voir MASTERPLAN.md §6.1 Phase 2 — automatic Demucs.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Background music indicator threshold
# test_002 (speech only) = 0.02, test_003 (music) = 0.34
# Threshold 0.10 cleanly separates them
_DEFAULT_THRESHOLD = 0.10


def detect_background_audio(
    audio_path: str | Path,
    sample_duration_s: float = 30.0,
    threshold: float = _DEFAULT_THRESHOLD,
) -> dict:
    """Analyse audio to detect background music/SFX.

    Uses short-time spectral analysis. Measures energy in music-typical
    frequency bands during quiet vocal moments. Background music persists
    during speech pauses; speech-only audio does not.

    Args:
        audio_path: Path to WAV audio file.
        sample_duration_s: Duration of analysis sample in seconds.
        threshold: bg_music_indicator above which Demucs is recommended.

    Returns:
        Dict with has_background, background_ratio, recommendation, details.
    """
    import soundfile as sf

    info = sf.info(str(audio_path))
    sr = info.samplerate
    total_s = info.duration

    # Read 30s sample from middle
    mid_s = total_s / 2
    start_s = max(0, mid_s - sample_duration_s / 2)
    start_sample = int(start_s * sr)
    n_samples = int(min(sample_duration_s, total_s) * sr)

    samples, sr = sf.read(str(audio_path), start=start_sample, frames=n_samples, dtype="float32")
    if samples.ndim > 1:
        samples = samples.mean(axis=1)

    if len(samples) < sr:
        logger.warning("Audio too short for background detection (%.1fs)", len(samples) / sr)
        return {
            "has_background": False,
            "background_ratio": 0.0,
            "recommendation": "no_demucs",
            "threshold": threshold,
            "details": {},
        }

    # Short-time spectral analysis
    frame_size = 2048
    hop = 1024
    n_frames = (len(samples) - frame_size) // hop

    music_energy_frames = []
    vocal_energy_frames = []

    for i in range(n_frames):
        frame = samples[i * hop : i * hop + frame_size]
        spec = np.abs(np.fft.rfft(frame)) ** 2
        freqs = np.fft.rfftfreq(frame_size, 1.0 / sr)

        # Music bands: bass instruments (200-500 Hz) + instruments/synths (2000-8000 Hz)
        music_low = np.mean(spec[(freqs >= 200) & (freqs < 500)])
        music_high = np.mean(spec[(freqs >= 2000) & (freqs < 8000)])
        # Vocal band: 300-3000 Hz
        vocal = np.mean(spec[(freqs >= 300) & (freqs < 3000)])

        music_energy_frames.append(music_low + music_high)
        vocal_energy_frames.append(vocal)

    music_arr = np.array(music_energy_frames)
    vocal_arr = np.array(vocal_energy_frames)

    # Key metric: music energy during quiet vocal moments
    # In speech-only audio, everything is quiet during pauses
    # In music, the background persists during speech pauses
    vocal_threshold = np.percentile(vocal_arr, 30)
    quiet_mask = vocal_arr < vocal_threshold
    music_during_quiet = music_arr[quiet_mask]

    if len(music_during_quiet) > 0 and np.mean(vocal_arr) > 0:
        bg_music_indicator = float(np.mean(music_during_quiet) / np.mean(vocal_arr))
    else:
        bg_music_indicator = 0.0

    # Spectral flatness as secondary indicator
    spec_full = np.abs(np.fft.rfft(samples)) ** 2
    geo_mean = np.exp(np.mean(np.log(spec_full + 1e-10)))
    arith_mean = np.mean(spec_full)
    spectral_flatness = float(geo_mean / arith_mean) if arith_mean > 0 else 0.0

    has_background = bg_music_indicator > threshold

    result = {
        "has_background": has_background,
        "background_ratio": round(bg_music_indicator, 4),
        "recommendation": "demucs" if has_background else "no_demucs",
        "threshold": threshold,
        "details": {
            "bg_music_indicator": round(bg_music_indicator, 4),
            "spectral_flatness": round(spectral_flatness, 6),
            "vocal_energy_mean": round(float(np.mean(vocal_arr)), 4),
            "music_during_quiet_mean": round(float(np.mean(music_during_quiet)), 4) if len(music_during_quiet) > 0 else 0.0,
            "sample_duration_s": round(len(samples) / sr, 1),
        },
    }

    logger.info(
        "Background detection: bg_indicator=%.4f threshold=%.2f -> %s",
        bg_music_indicator, threshold, result["recommendation"],
    )
    return result
