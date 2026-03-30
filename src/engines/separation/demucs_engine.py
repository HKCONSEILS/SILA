"""Demucs v4 separation engine — Phase 2.

Voir MASTERPLAN.md §3.1 — Demucs v4 (htdemucs_ft).
V3: automatic chunking for long audio (>5 min) to prevent OOM.
Chunks extracted via ffmpeg (no full audio in RAM), stems written to disk per chunk.
"""

from __future__ import annotations

import gc
import logging
import os
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from src.engines.separation.interface import SeparationInterface, SeparationResult

logger = logging.getLogger(__name__)

DEMUCS_MAX_DURATION_S = 300   # 5 min per chunk
DEMUCS_OVERLAP_S = 10         # 10s overlap


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

        Auto-chunks audio > 5 min to prevent OOM.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        vocals_path = output_dir / "vocals.wav"
        accomp_path = output_dir / "accompaniment.wav"

        info = sf.info(str(audio_path))
        duration_s = info.duration
        orig_sr = info.samplerate

        if duration_s <= DEMUCS_MAX_DURATION_S:
            logger.info("Demucs single-pass (%.0fs <= %ds)", duration_s, DEMUCS_MAX_DURATION_S)
            return self._separate_single(audio_path, output_dir, vocals_path, accomp_path)
        else:
            logger.info("Demucs chunked mode (%.0fs > %ds)", duration_s, DEMUCS_MAX_DURATION_S)
            return self._separate_chunked(audio_path, output_dir, vocals_path, accomp_path,
                                          duration_s, orig_sr)

    def _run_demucs_on_audio(self, audio_np: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
        """Run Demucs on a numpy array. Returns (vocals_mono, accomp_mono) at original sr."""
        from demucs.apply import apply_model

        self._load()
        model_sr = self._model.samplerate

        # Make stereo
        if audio_np.ndim == 1:
            stereo = np.stack([audio_np, audio_np])
        else:
            stereo = audio_np.T if audio_np.shape[1] == 2 else audio_np

        # Resample to model SR
        if sr != model_sr:
            import librosa
            ch0 = librosa.resample(stereo[0], orig_sr=sr, target_sr=model_sr)
            ch1 = librosa.resample(stereo[1], orig_sr=sr, target_sr=model_sr)
            stereo = np.stack([ch0, ch1])

        tensor = torch.tensor(stereo, dtype=torch.float32).unsqueeze(0).to(self.device)

        with torch.no_grad():
            sources = apply_model(self._model, tensor, device=self.device)

        source_names = self._model.sources
        vocals_idx = source_names.index("vocals")

        vocals = sources[0, vocals_idx].cpu().numpy().mean(axis=0)
        accomp = sum(
            sources[0, j].cpu().numpy()
            for j in range(len(source_names))
            if j != vocals_idx
        ).mean(axis=0)

        # Free GPU
        del sources, tensor
        torch.cuda.empty_cache()

        # Resample back
        if model_sr != sr:
            import librosa
            vocals = librosa.resample(vocals, orig_sr=model_sr, target_sr=sr)
            accomp = librosa.resample(accomp, orig_sr=model_sr, target_sr=sr)

        return vocals, accomp

    def _separate_single(self, audio_path: Path, output_dir: Path,
                         vocals_path: Path, accomp_path: Path) -> SeparationResult:
        """Single-pass separation for short audio."""
        audio, sr = sf.read(str(audio_path), dtype="float32")
        if audio.ndim > 1:
            audio_mono = audio[:, 0]
        else:
            audio_mono = audio

        logger.info("Running Demucs on %s (%.1fs)...", audio_path.name, len(audio_mono) / sr)
        vocals, accomp = self._run_demucs_on_audio(audio_mono, sr)

        sf.write(str(vocals_path), vocals, sr)
        sf.write(str(accomp_path), accomp, sr)

        logger.info("Demucs done: vocals=%s, accomp=%s", vocals_path, accomp_path)
        return SeparationResult(voice_path=vocals_path, music_path=accomp_path, sfx_path=accomp_path)

    def _separate_chunked(self, audio_path: Path, output_dir: Path,
                          vocals_path: Path, accomp_path: Path,
                          duration_s: float, orig_sr: int) -> SeparationResult:
        """Chunked separation — stream from disk, never load full audio in RAM."""
        self._load()

        chunk_dur = DEMUCS_MAX_DURATION_S
        overlap = DEMUCS_OVERLAP_S

        # Calculate chunk positions (in seconds)
        positions = []
        start_s = 0.0
        while start_s < duration_s:
            end_s = min(start_s + chunk_dur, duration_s)
            positions.append((start_s, end_s))
            if end_s >= duration_s:
                break
            start_s = end_s - overlap

        logger.info("Demucs chunking: %d chunks of %ds with %ds overlap (%.0fs total)",
                    len(positions), chunk_dur, overlap, duration_s)

        # Process each chunk: extract via ffmpeg -> Demucs -> write stems to disk
        chunk_stem_files = []
        tmp_dir = output_dir / "_demucs_chunks"
        tmp_dir.mkdir(exist_ok=True)

        for i, (s, e) in enumerate(positions):
            logger.info("Demucs chunk %d/%d: %.0fs -> %.0fs", i + 1, len(positions), s, e)

            # Extract chunk via ffmpeg (no full audio in RAM)
            chunk_wav = tmp_dir / f"chunk_{i}.wav"
            subprocess.run([
                "ffmpeg", "-y", "-i", str(audio_path),
                "-ss", str(s), "-t", str(e - s),
                "-ar", str(orig_sr), "-ac", "1", "-acodec", "pcm_f32le",
                str(chunk_wav),
            ], capture_output=True, check=True)

            # Read chunk and run Demucs
            chunk_audio, sr = sf.read(str(chunk_wav), dtype="float32")
            vocals_chunk, accomp_chunk = self._run_demucs_on_audio(chunk_audio, sr)

            # Write stems to disk immediately
            v_path = tmp_dir / f"vocals_{i}.wav"
            a_path = tmp_dir / f"accomp_{i}.wav"
            sf.write(str(v_path), vocals_chunk, sr)
            sf.write(str(a_path), accomp_chunk, sr)

            chunk_stem_files.append({"vocals": v_path, "accomp": a_path})

            # Free RAM
            del chunk_audio, vocals_chunk, accomp_chunk
            gc.collect()

            # Remove source chunk
            chunk_wav.unlink(missing_ok=True)

            logger.info("Chunk %d/%d done, stems saved to disk", i + 1, len(positions))

        # Concatenate stems from disk (streaming, one chunk at a time)
        overlap_samples = int(overlap * orig_sr)
        self._concat_stems_streaming(chunk_stem_files, "vocals", vocals_path, overlap_samples, orig_sr)
        self._concat_stems_streaming(chunk_stem_files, "accomp", accomp_path, overlap_samples, orig_sr)

        # Cleanup temp files
        for cf in chunk_stem_files:
            cf["vocals"].unlink(missing_ok=True)
            cf["accomp"].unlink(missing_ok=True)
        tmp_dir.rmdir()

        logger.info("Demucs chunked done: %d chunks -> vocals=%s, accomp=%s",
                    len(positions), vocals_path, accomp_path)

        return SeparationResult(voice_path=vocals_path, music_path=accomp_path, sfx_path=accomp_path)

    @staticmethod
    def _concat_stems_streaming(chunk_files: list[dict], key: str,
                                 output_path: Path, overlap_samples: int, sr: int):
        """Concatenate stems from disk files with crossfade. Reads one chunk at a time."""
        n = len(chunk_files)
        writer = sf.SoundFile(str(output_path), mode="w", samplerate=sr,
                              channels=1, subtype="FLOAT")
        prev_tail = None

        for i, cf in enumerate(chunk_files):
            chunk, _ = sf.read(str(cf[key]), dtype="float32")

            if n == 1:
                writer.write(chunk)
            elif i == 0:
                # First: write all except overlap tail
                writer.write(chunk[:-overlap_samples])
                prev_tail = chunk[-overlap_samples:].copy()
            elif i == n - 1:
                # Last: crossfade head, write rest
                head = chunk[:overlap_samples]
                fade_in = np.linspace(0.0, 1.0, overlap_samples, dtype=np.float32)
                fade_out = np.linspace(1.0, 0.0, overlap_samples, dtype=np.float32)
                crossfaded = prev_tail * fade_out + head * fade_in
                writer.write(crossfaded)
                writer.write(chunk[overlap_samples:])
            else:
                # Middle: crossfade head, write body, save tail
                head = chunk[:overlap_samples]
                fade_in = np.linspace(0.0, 1.0, overlap_samples, dtype=np.float32)
                fade_out = np.linspace(1.0, 0.0, overlap_samples, dtype=np.float32)
                crossfaded = prev_tail * fade_out + head * fade_in
                writer.write(crossfaded)
                writer.write(chunk[overlap_samples:-overlap_samples])
                prev_tail = chunk[-overlap_samples:].copy()

            del chunk

        writer.close()

    def unload(self):
        self._model = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("Demucs unloaded.")
