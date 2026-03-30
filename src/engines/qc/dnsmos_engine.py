"""DNSMOS quality scoring engine — Phase 10 V2.

Uses Microsoft DNSMOS (via speechmos) to estimate Mean Opinion Score
for TTS audio quality. Replaces UTMOS (unavailable as pip package).

MOS scale: 1.0 (bad) to 5.0 (excellent).
Target: ovrl_mos > 3.0 for acceptable quality.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# DNSMOS requires 16kHz input
_DNSMOS_SR = 16000


class DNSMOSEngine:
    """DNSMOS quality scorer for TTS segments."""

    def __init__(self):
        self._initialized = False

    def _ensure_init(self):
        if not self._initialized:
            from speechmos import dnsmos  # noqa: F401
            self._initialized = True

    def score(self, audio_path: str | Path) -> dict:
        """Score a single audio file.

        Returns dict with ovrl_mos, sig_mos, bak_mos, p808_mos.
        """
        self._ensure_init()
        import soundfile as sf
        import librosa
        from speechmos import dnsmos

        audio, sr = sf.read(str(audio_path), dtype="float32")
        if audio.ndim > 1:
            audio = audio[:, 0]

        # Resample to 16kHz if needed
        if sr != _DNSMOS_SR:
            audio = librosa.resample(audio, orig_sr=sr, target_sr=_DNSMOS_SR)

        # Clip to [-1, 1] as required by DNSMOS
        audio = np.clip(audio, -1.0, 1.0)

        # Skip very short audio (< 0.5s)
        if len(audio) < _DNSMOS_SR * 0.5:
            logger.warning("Audio too short for DNSMOS: %s (%.2fs)", audio_path, len(audio) / _DNSMOS_SR)
            return {"ovrl_mos": 0.0, "sig_mos": 0.0, "bak_mos": 0.0, "p808_mos": 0.0}

        try:
            result = dnsmos.run(audio, _DNSMOS_SR, return_df=True, verbose=False)
            return {
                "ovrl_mos": round(float(result.get("ovrl_mos", 0.0)), 3),
                "sig_mos": round(float(result.get("sig_mos", 0.0)), 3),
                "bak_mos": round(float(result.get("bak_mos", 0.0)), 3),
                "p808_mos": round(float(result.get("p808_mos", 0.0)), 3),
            }
        except Exception as exc:
            logger.warning("DNSMOS scoring failed for %s: %s", audio_path, exc)
            return {"ovrl_mos": 0.0, "sig_mos": 0.0, "bak_mos": 0.0, "p808_mos": 0.0}

    def score_batch(self, audio_paths: list[str | Path]) -> list[dict]:
        """Score multiple audio files."""
        return [self.score(p) for p in audio_paths]
