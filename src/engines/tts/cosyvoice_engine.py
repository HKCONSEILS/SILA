"""CosyVoice 3.0 TTS engine — Phase 8.

Voir MASTERPLAN.md §3.1 — CosyVoice 3.0 (0.5B).
CosyVoice3 cross-lingual: requires 'You are a helpful assistant.<|endofprompt|>' prefix.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from src.engines.tts.interface import TTSInterface, TTSResult

logger = logging.getLogger(__name__)

# CosyVoice3 requires this prefix for cross-lingual synthesis
COSYVOICE3_PREFIX = "You are a helpful assistant.<|endofprompt|>"


class CosyVoiceEngine(TTSInterface):
    """CosyVoice 3.0 cross-lingual TTS."""

    def __init__(
        self,
        model_dir: str | Path,
        cosyvoice_repo: str = "/opt/sila/CosyVoice",
        device: str = "cuda",
    ):
        self.model_dir = str(model_dir)
        self.cosyvoice_repo = cosyvoice_repo
        self.device = device
        self._model = None
        self._voice_ref_path = None

    def _ensure_path(self):
        paths = [
            self.cosyvoice_repo,
            os.path.join(self.cosyvoice_repo, "third_party", "Matcha-TTS"),
        ]
        for p in paths:
            if p not in sys.path:
                sys.path.insert(0, p)

    def _load(self):
        if self._model is not None:
            return
        self._ensure_path()
        from cosyvoice.cli.cosyvoice import CosyVoice3

        logger.info("Loading CosyVoice3 from %s...", self.model_dir)
        self._model = CosyVoice3(self.model_dir)
        logger.info("CosyVoice3 loaded. Sample rate: %d", self._model.sample_rate)

    def set_voice_reference(self, audio_path: Path, start_ms: int = 0, end_ms: int = 10000):
        """Extract a voice reference segment and save to WAV file."""
        audio, sr = sf.read(str(audio_path), dtype="float32")
        if audio.ndim > 1:
            audio = audio[:, 0]

        start_sample = int(start_ms * sr / 1000)
        end_sample = min(int(end_ms * sr / 1000), len(audio))
        segment = audio[start_sample:end_sample]

        ref_dir = Path(audio_path).parent.parent / "voice_refs"
        ref_dir.mkdir(parents=True, exist_ok=True)
        ref_path = ref_dir / "voice_ref.wav"
        sf.write(str(ref_path), segment, sr)
        self._voice_ref_path = str(ref_path)

        logger.info(
            "Voice reference saved: %.1fs from %s -> %s",
            len(segment) / sr, audio_path, ref_path,
        )

    def synthesize(
        self,
        text: str,
        voice_profile: Path | None = None,
        target_lang: str = "en",
        output_path: Path | None = None,
        speed: float = 1.0,
    ) -> TTSResult:
        """Synthesize speech using cross-lingual voice cloning.
        
        CosyVoice3 requires the text to be prefixed with:
        'You are a helpful assistant.<|endofprompt|>'
        """
        self._load()

        if self._voice_ref_path is None:
            raise RuntimeError("Voice reference not set. Call set_voice_reference() first.")

        # Add CosyVoice3 prefix if not already present
        if "<|endofprompt|>" not in text:
            prefixed_text = f"{COSYVOICE3_PREFIX}{text}"
        else:
            prefixed_text = text

        # Collect all chunks from the generator
        all_audio = []
        try:
            for chunk in self._model.inference_cross_lingual(
                tts_text=prefixed_text,
                prompt_wav=self._voice_ref_path,
                stream=False,
                speed=speed,
                text_frontend=False,  # CosyVoice3: we handle prefixing ourselves
            ):
                audio_chunk = chunk["tts_speech"].squeeze().cpu().numpy()
                all_audio.append(audio_chunk)
        except Exception as e:
            logger.warning("TTS synthesis error for '%s': %s. Generating silence.", text[:50], e)
            # Generate silence matching approximate duration (10 chars/sec estimate)
            silence_duration = max(1.0, len(text) * 0.08)
            silence = np.zeros(int(silence_duration * 24000), dtype=np.float32)
            all_audio = [silence]

        if not all_audio:
            silence = np.zeros(24000, dtype=np.float32)  # 1s silence
            all_audio = [silence]

        audio = np.concatenate(all_audio)
        sample_rate = self._model.sample_rate if self._model else 24000
        duration_ms = int(len(audio) / sample_rate * 1000)

        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(output_path), audio, sample_rate)

        return TTSResult(
            audio_path=output_path or Path("/dev/null"),
            duration_ms=duration_ms,
            sample_rate=sample_rate,
        )

    def unload(self):
        import gc
        self._model = None
        self._voice_ref_path = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("CosyVoice unloaded.")
