"""Demucs v4 separation engine — Phase 2.

Voir MASTERPLAN.md §3.1 — Demucs v4 (htdemucs_ft).
Extrait la voix propre pour ASR et reference TTS.
"""

from __future__ import annotations

import gc
import logging
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from src.engines.separation.interface import SeparationInterface, SeparationResult

logger = logging.getLogger(__name__)


class DemucsEngine(SeparationInterface):
    """Demucs htdemucs_ft pour separation vocale."""

    def __init__(self, model_name: str = "htdemucs_ft", device: str = "cuda"):
        self.model_name = model_name
        self.device = device
        self._model = None

    def _load(self):
        if self._model is not None:
            return
        from demucs.pretrained import get_model
        logger.info("Loading Demucs model %s on %s...", self.model_name, self.device)
        self._model = get_model(self.model_name)
        self._model.to(self.device)
        self._model.eval()
        logger.info("Demucs loaded. Sources: %s", self._model.sources)

    def separate(self, audio_path: Path, output_dir: Path) -> SeparationResult:
        """Separe l'audio en vocals + accompaniment.

        Args:
            audio_path: WAV 48kHz mono.
            output_dir: Dossier de sortie.

        Returns:
            SeparationResult avec chemins vocals.wav et accompaniment.wav.
        """
        from demucs.apply import apply_model

        self._load()

        output_dir.mkdir(parents=True, exist_ok=True)
        vocals_path = output_dir / "vocals.wav"
        accomp_path = output_dir / "accompaniment.wav"

        # Load audio
        audio, sr = sf.read(str(audio_path), dtype="float32")
        if audio.ndim == 1:
            audio = np.stack([audio, audio])  # Demucs needs stereo
        elif audio.ndim == 2:
            audio = audio.T  # (samples, channels) -> (channels, samples)

        # Resample to Demucs sample rate if needed
        model_sr = self._model.samplerate
        if sr != model_sr:
            import librosa
            ch0 = librosa.resample(audio[0], orig_sr=sr, target_sr=model_sr)
            ch1 = librosa.resample(audio[1], orig_sr=sr, target_sr=model_sr)
            audio = np.stack([ch0, ch1])
            logger.info("Resampled %d -> %d Hz for Demucs", sr, model_sr)

        # Convert to tensor: (batch=1, channels=2, samples)
        tensor = torch.tensor(audio, dtype=torch.float32).unsqueeze(0).to(self.device)

        logger.info("Running Demucs separation on %s (%.1fs)...",
                     audio_path.name, audio.shape[1] / model_sr)

        with torch.no_grad():
            sources = apply_model(self._model, tensor, device=self.device)
        # sources shape: (batch, n_sources, channels, samples)

        source_names = self._model.sources  # e.g. ['drums', 'bass', 'other', 'vocals']
        vocals_idx = source_names.index("vocals")

        # Extract vocals
        vocals = sources[0, vocals_idx].cpu().numpy()  # (channels, samples)
        vocals_mono = vocals.mean(axis=0)  # mono

        # Extract accompaniment (everything except vocals)
        accomp = sum(
            sources[0, i].cpu().numpy()
            for i in range(len(source_names))
            if i != vocals_idx
        )
        accomp_mono = accomp.mean(axis=0)

        # Resample back to original sr if needed
        if model_sr != sr:
            import librosa
            vocals_mono = librosa.resample(vocals_mono, orig_sr=model_sr, target_sr=sr)
            accomp_mono = librosa.resample(accomp_mono, orig_sr=model_sr, target_sr=sr)

        # Save
        sf.write(str(vocals_path), vocals_mono, sr)
        sf.write(str(accomp_path), accomp_mono, sr)

        logger.info("Demucs separation done: vocals=%s, accompaniment=%s",
                     vocals_path, accomp_path)

        return SeparationResult(
            voice_path=vocals_path,
            music_path=accomp_path,
            sfx_path=accomp_path,  # V1: sfx = accompaniment
        )

    def unload(self):
        self._model = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("Demucs unloaded.")
