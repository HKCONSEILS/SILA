"""Placement des segments TTS sur la timeline + crossfade + background mix.

Voir MASTERPLAN.md §6.1 Phase 9 — Assembly audio.
Principe P8 : crossfade 50 ms entre segments TTS finaux.
V2 : mix TTS + fond sonore (Demucs stems) avec ducking automatique.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def apply_ducking(
    background: np.ndarray,
    voice_mask: np.ndarray,
    duck_db: float = -6.0,
    fade_ms: int = 50,
    expand_ms: int = 100,
    sample_rate: int = 48000,
) -> np.ndarray:
    """Apply ducking to background audio where voice is active.

    Reduces background volume by duck_db during voice segments,
    with smooth fade transitions.

    Args:
        background: Background audio array (mono, float32).
        voice_mask: Boolean array, True where TTS voice is present.
        duck_db: Attenuation in dB during voice (-6dB = broadcast standard).
        fade_ms: Fade duration in ms for smooth transitions.
        expand_ms: Expand voice mask by this amount on each side.
        sample_rate: Sample rate in Hz.

    Returns:
        Ducked background audio.
    """
    duck_factor = 10 ** (duck_db / 20)  # -6dB -> ~0.501

    # Expand the voice mask by expand_ms on each side
    expand_samples = int(expand_ms / 1000 * sample_rate)
    expanded = voice_mask.copy()
    if expand_samples > 0:
        # Expand right
        for shift in range(1, expand_samples + 1):
            expanded[shift:] |= voice_mask[:-shift]
        # Expand left
        for shift in range(1, expand_samples + 1):
            expanded[:-shift] |= voice_mask[shift:]

    # Build gain envelope
    gain = np.ones(len(background), dtype=np.float32)
    gain[expanded] = duck_factor

    # Smooth transitions with uniform filter
    fade_samples = max(int(fade_ms / 1000 * sample_rate), 1)
    from scipy.ndimage import uniform_filter1d
    gain = uniform_filter1d(gain, size=fade_samples).astype(np.float32)

    return background * gain


def assemble_segments(
    segments: list[dict],
    output_path: Path,
    total_duration_ms: int,
    crossfade_ms: int = 50,
    sample_rate: int = 48000,
    background_audio_path: Path | None = None,
    duck_db: float = -6.0,
) -> Path:
    """Place les segments TTS sur la timeline cible avec crossfade.

    Voir MASTERPLAN.md §6.1 Phase 9.1-9.2.
    V2: optionally mixes with background audio (Demucs stems) + ducking.

    Args:
        segments: Liste de dicts avec 'audio_path', 'start_ms', 'duration_ms'.
        output_path: Chemin de sortie du mix.
        total_duration_ms: Duree totale de la timeline en ms.
        crossfade_ms: Duree du crossfade inter-segments (50ms par defaut, P8).
        sample_rate: Frequence d'echantillonnage (48kHz, P5).
        background_audio_path: Optional path to background audio (Demucs accompaniment).
        duck_db: Ducking attenuation in dB (default -6dB).

    Returns:
        Chemin vers le fichier audio assemble.
    """
    import soundfile as sf

    total_samples = int(total_duration_ms * sample_rate / 1000)
    timeline = np.zeros(total_samples, dtype=np.float32)
    voice_mask = np.zeros(total_samples, dtype=bool)
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

        # Crossfade ONLY on actual overlap with previous segment content
        has_content_before = np.any(timeline[start_sample:min(start_sample + crossfade_samples, end_sample)] != 0)
        if crossfade_samples > 0 and has_content_before:
            fade_len = min(crossfade_samples, seg_length)
            fade_in = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
            chunk[:fade_len] *= fade_in
            fade_out = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
            timeline[start_sample : start_sample + fade_len] *= fade_out

        timeline[start_sample:end_sample] += chunk
        voice_mask[start_sample:end_sample] = True

    # Save voice-only timeline (before background mix) for multi-track export
    voice_only_path = output_path.parent / output_path.name.replace("_raw.wav", "_voice_only.wav")
    sf.write(str(voice_only_path), timeline.copy(), sample_rate)
    logger.info("Voice-only saved: %s", voice_only_path)

    # Mix with background audio if provided (V2: Demucs stems)
    if background_audio_path is not None and Path(background_audio_path).exists():
        logger.info("Mixing TTS with background audio: %s", background_audio_path)
        bg_audio, bg_sr = sf.read(str(background_audio_path), dtype="float32")
        if bg_audio.ndim > 1:
            bg_audio = bg_audio.mean(axis=1)  # stereo -> mono

        # Resample background to timeline sample rate if needed
        if bg_sr != sample_rate:
            import librosa
            bg_audio = librosa.resample(bg_audio, orig_sr=bg_sr, target_sr=sample_rate)
            logger.info("Resampled background %d -> %d Hz", bg_sr, sample_rate)

        # Match lengths
        if len(bg_audio) > total_samples:
            bg_audio = bg_audio[:total_samples]
        elif len(bg_audio) < total_samples:
            bg_audio = np.pad(bg_audio, (0, total_samples - len(bg_audio)))

        # Apply ducking
        bg_ducked = apply_ducking(
            bg_audio, voice_mask,
            duck_db=duck_db,
            sample_rate=sample_rate,
        )
        logger.info("Ducking applied: %.1f dB attenuation during voice segments", duck_db)

        # Save ducked background for multi-track export
        bg_only_path = output_path.parent / output_path.name.replace("_raw.wav", "_background.wav")
        sf.write(str(bg_only_path), bg_ducked, sample_rate)
        logger.info("Background-only saved: %s", bg_only_path)

        # Mix: TTS timeline + ducked background
        timeline = timeline + bg_ducked
        logger.info("Background mixed into timeline")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), timeline, sample_rate)
    logger.info("Assembly done: %s (%d ms, %d segments, bg=%s)",
                output_path, total_duration_ms, len(segments),
                "yes" if background_audio_path else "no")
    return output_path
