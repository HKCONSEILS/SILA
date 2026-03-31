"""MOSS-TTS engine — duration-controlled TTS via subprocess.

Uses MossTTSLocal 1.7B with tokens=round(timing_budget_ms/80) for
precise duration control. Model runs in isolated venv via subprocess.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from src.engines.tts.interface import TTSInterface, TTSResult

logger = logging.getLogger(__name__)

MOSS_VENV_PYTHON = "/opt/sila/bench/moss-tts-venv/bin/python"
MOSS_INFER_SCRIPT = "/opt/sila/bench/moss_tts_infer.py"
MOSS_SAMPLE_RATE = 24000
PIPELINE_SAMPLE_RATE = 48000


class MossTTSEngine(TTSInterface):
    """MOSS-TTS with duration control via subprocess batch inference."""

    @property
    def supports_duration_control(self) -> bool:
        return True

    @property
    def supports_speed_control(self) -> bool:
        return False

    def __init__(self, **kwargs):
        self._voice_ref_path = None
        self._batch_segments = []
        self._batch_results = {}

    def set_voice_reference(self, audio_path: Path, start_ms: int = 0, end_ms: int = 10000):
        """Set voice reference for cloning."""
        self._voice_ref_path = str(audio_path)
        logger.info("MOSS voice reference set: %s", audio_path)

    def set_voice_reference_multi(self, audio_path: Path, segments: list,
                                   n_best: int = 5, max_duration_s: float = 30.0):
        """Build multi-segment voice reference (same as CosyVoice P6)."""
        # MOSS-TTS accepts a reference WAV directly
        # Build a concatenated reference from best segments
        MIN_SEG_MS = 3000
        MAX_SEG_MS = 12000
        MIN_CONFIDENCE = 0.7

        audio, sr = sf.read(str(audio_path), dtype="float32")
        if audio.ndim > 1:
            audio = audio[:, 0]

        candidates = []
        for seg in segments:
            dur_ms = seg.get("duration_ms", seg.get("end_ms", 0) - seg.get("start_ms", 0))
            if dur_ms < MIN_SEG_MS or dur_ms > MAX_SEG_MS:
                continue
            words = seg.get("words", [])
            if not words:
                continue
            avg_conf = sum(w.get("confidence", 0) for w in words) / len(words)
            if avg_conf < MIN_CONFIDENCE:
                continue
            candidates.append((seg, avg_conf, dur_ms))

        candidates.sort(key=lambda x: x[1], reverse=True)
        selected = candidates[:n_best]

        if not selected:
            # Fallback: first 10s
            end_sample = min(int(10 * sr), len(audio))
            ref_audio = audio[:end_sample]
        else:
            selected.sort(key=lambda x: x[0].get("start_ms", 0))
            clips = []
            total_s = 0.0
            for seg, _, _ in selected:
                if total_s >= max_duration_s:
                    break
                start = int(seg.get("start_ms", 0) * sr / 1000)
                end = min(int(seg.get("end_ms", 0) * sr / 1000), len(audio))
                clip = audio[start:end]
                clips.append(clip)
                total_s += len(clip) / sr
            ref_audio = np.concatenate(clips)

        ref_dir = Path(audio_path).parent.parent / "voice_refs"
        ref_dir.mkdir(parents=True, exist_ok=True)
        ref_path = ref_dir / "moss_voice_ref.wav"
        sf.write(str(ref_path), ref_audio, sr)
        self._voice_ref_path = str(ref_path)
        logger.info("MOSS multi-segment voice ref: %d clips, %.1fs -> %s",
                     len(selected), len(ref_audio) / sr, ref_path)
        return len(selected)

    def synthesize(self, text: str, voice_profile: Path | None = None,
                   target_lang: str = "en", output_path: Path | None = None,
                   speed: float = 1.0, **kwargs) -> TTSResult:
        """Synthesize a single segment via MOSS-TTS subprocess."""
        timing_budget_ms = kwargs.get("timing_budget_ms", None)
        target_tokens = round(timing_budget_ms / 80) if timing_budget_ms else None

        # Create temp batch file with single segment
        seg_data = {"segment_id": "single", "text": text}
        if target_tokens:
            seg_data["tokens"] = target_tokens

        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write(json.dumps(seg_data) + "\n")
            batch_path = f.name

        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [MOSS_VENV_PYTHON, MOSS_INFER_SCRIPT,
                   '--batch', batch_path, '--output-dir', tmpdir]
            if self._voice_ref_path:
                cmd.extend(['--reference', self._voice_ref_path])

            env = os.environ.copy()
            env['CUDA_VISIBLE_DEVICES'] = os.environ.get('CUDA_VISIBLE_DEVICES', '2')

            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=300, env=env)

            os.unlink(batch_path)

            if result.returncode != 0:
                logger.error("MOSS-TTS failed: %s", result.stderr[-500:])
                raise RuntimeError(f"MOSS-TTS inference failed: {result.stderr[-200:]}")

            # Read results
            results = json.load(open(os.path.join(tmpdir, 'moss_results.json')))
            seg_result = results[0]

            # Resample 24kHz -> 48kHz
            moss_wav = seg_result['audio_path']
            audio, sr = sf.read(moss_wav, dtype='float32')

            if sr != PIPELINE_SAMPLE_RATE:
                import librosa
                audio = librosa.resample(audio, orig_sr=sr, target_sr=PIPELINE_SAMPLE_RATE)

            if output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                sf.write(str(output_path), audio, PIPELINE_SAMPLE_RATE)

            return TTSResult(
                audio_path=output_path or Path("/dev/null"),
                duration_ms=seg_result['duration_ms'],
                sample_rate=PIPELINE_SAMPLE_RATE,
            )

    def synthesize_batch(self, segments_data: list[dict], output_dir: Path) -> list[dict]:
        """Synthesize all segments in one subprocess call (efficient)."""
        output_dir.mkdir(parents=True, exist_ok=True)

        # Write batch JSONL
        batch_path = output_dir / "moss_batch.jsonl"
        with open(batch_path, 'w') as f:
            for seg in segments_data:
                tokens = round(seg['timing_budget_ms'] / 80)
                f.write(json.dumps({
                    'segment_id': seg['segment_id'],
                    'text': seg['text'],
                    'tokens': tokens,
                }) + "\n")

        # Run batch inference
        moss_output_dir = output_dir / "moss_raw"
        moss_output_dir.mkdir(exist_ok=True)

        cmd = [MOSS_VENV_PYTHON, MOSS_INFER_SCRIPT,
               '--batch', str(batch_path), '--output-dir', str(moss_output_dir)]
        if self._voice_ref_path:
            cmd.extend(['--reference', self._voice_ref_path])

        env = os.environ.copy()
        env['CUDA_VISIBLE_DEVICES'] = os.environ.get('CUDA_VISIBLE_DEVICES', '2')

        logger.info("MOSS-TTS batch: %d segments", len(segments_data))
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=3600, env=env)

        if result.returncode != 0:
            logger.error("MOSS-TTS batch failed: %s", result.stderr[-500:])
            raise RuntimeError(f"MOSS-TTS batch failed: {result.stderr[-200:]}")

        summary = json.loads(result.stdout)
        logger.info("MOSS-TTS batch done: %d segs, %ds total, VRAM %.2f Go",
                     summary['total_segments'], summary['total_inference_s'],
                     summary['vram_peak_gb'])

        # Load results and resample to 48kHz
        moss_results = json.load(open(str(moss_output_dir / "moss_results.json")))
        final_results = []

        for r in moss_results:
            moss_wav = r['audio_path']
            audio, sr = sf.read(moss_wav, dtype='float32')
            if sr != PIPELINE_SAMPLE_RATE:
                import librosa
                audio = librosa.resample(audio, orig_sr=sr, target_sr=PIPELINE_SAMPLE_RATE)

            final_path = output_dir / f"{r['segment_id']}.wav"
            sf.write(str(final_path), audio, PIPELINE_SAMPLE_RATE)

            final_results.append({
                'segment_id': r['segment_id'],
                'audio_path': str(final_path),
                'duration_ms': r['duration_ms'],
                'sample_rate': PIPELINE_SAMPLE_RATE,
                'inference_s': r['inference_s'],
                'tokens_requested': r['tokens_requested'],
            })

        return final_results

    def unload(self):
        """Nothing to unload — model runs in subprocess."""
        logger.info("MOSS-TTS engine: nothing to unload (subprocess mode)")
