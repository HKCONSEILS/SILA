"""MOSS-TTS engine — HTTP server mode with subprocess fallback.

Primary: POST to persistent MOSS-TTS server on port 8082
(model loaded once, ~3s/segment instead of ~25s).

Fallback: subprocess batch inference if server is down
(loads model per batch, slower but always works).

See SILA_Masterplan.md §3.1 — MossTTSLocal 1.7B.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from src.engines.tts.interface import TTSInterface, TTSResult

logger = logging.getLogger(__name__)

MOSS_VENV_PYTHON = "/opt/sila/bench/moss-tts-venv/bin/python"
MOSS_INFER_SCRIPT = "/opt/sila/bench/moss_tts_infer.py"
MOSS_SERVER_URL = "http://localhost:8082"
MOSS_SAMPLE_RATE = 24000
PIPELINE_SAMPLE_RATE = 48000


class MossTTSEngine(TTSInterface):
    """MOSS-TTS with HTTP server (preferred) or subprocess fallback."""

    @property
    def supports_duration_control(self) -> bool:
        return True

    @property
    def supports_speed_control(self) -> bool:
        return False

    def __init__(self, server_url: str = MOSS_SERVER_URL, **kwargs):
        self._server_url = server_url
        self._voice_ref_path = None
        self._batch_segments = []
        self._batch_results = {}
        self._http_available = self._check_server()

    def _check_server(self) -> bool:
        """Check if the MOSS-TTS HTTP server is running."""
        try:
            import requests
            r = requests.get(f"{self._server_url}/health", timeout=5)
            r.raise_for_status()
            info = r.json()
            logger.info(
                "MOSS-TTS HTTP server OK: model=%s, vram=%.2f Go",
                info.get("model", "?"), info.get("vram_gb", 0),
            )
            return True
        except Exception as exc:
            logger.warning(
                "MOSS-TTS HTTP server not available (%s), will use subprocess fallback",
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Voice reference (shared between HTTP and subprocess modes)
    # ------------------------------------------------------------------

    def set_voice_reference(self, audio_path: Path, start_ms: int = 0, end_ms: int = 10000):
        """Set voice reference for cloning."""
        self._voice_ref_path = str(audio_path)
        logger.info("MOSS voice reference set: %s", audio_path)

    def set_voice_reference_multi(self, audio_path: Path, segments: list,
                                   n_best: int = 5, max_duration_s: float = 30.0):
        """Build multi-segment voice reference (same as CosyVoice P6)."""
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

    # ------------------------------------------------------------------
    # Resample helper
    # ------------------------------------------------------------------

    @staticmethod
    def _resample_to_pipeline(audio: np.ndarray, sr: int) -> np.ndarray:
        """Resample from MOSS native rate to pipeline 48kHz."""
        if sr == PIPELINE_SAMPLE_RATE:
            return audio
        import librosa
        return librosa.resample(audio, orig_sr=sr, target_sr=PIPELINE_SAMPLE_RATE)

    # ------------------------------------------------------------------
    # HTTP mode
    # ------------------------------------------------------------------

    def _synthesize_http(self, text: str, output_path: Path,
                         timing_budget_ms: int | None = None) -> TTSResult:
        """Synthesize via HTTP server (fast — model already loaded)."""
        import requests

        # Server writes WAV at 24kHz, we resample after
        server_out = output_path.with_suffix(".moss_raw.wav")

        payload = {"text": text, "output_path": str(server_out)}
        if timing_budget_ms:
            payload["tokens"] = round(timing_budget_ms / 80)
        if self._voice_ref_path:
            payload["reference"] = self._voice_ref_path

        r = requests.post(
            f"{self._server_url}/synthesize",
            json=payload, timeout=300,
        )
        r.raise_for_status()
        result = r.json()

        # Resample 24kHz -> 48kHz
        audio, sr = sf.read(str(server_out), dtype="float32")
        audio = self._resample_to_pipeline(audio, sr)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), audio, PIPELINE_SAMPLE_RATE)

        # Clean up raw file
        server_out.unlink(missing_ok=True)

        return TTSResult(
            audio_path=output_path,
            duration_ms=result["duration_ms"],
            sample_rate=PIPELINE_SAMPLE_RATE,
        )

    def _synthesize_batch_http(self, segments_data: list[dict],
                                output_dir: Path) -> list[dict]:
        """Batch synthesis via HTTP server."""
        import requests

        output_dir.mkdir(parents=True, exist_ok=True)
        raw_dir = output_dir / "moss_raw"
        raw_dir.mkdir(exist_ok=True)

        batch_segments = []
        for seg in segments_data:
            tokens = round(seg["timing_budget_ms"] / 80)
            raw_path = str(raw_dir / f"{seg['segment_id']}.wav")
            entry = {
                "id": seg["segment_id"],
                "text": seg["text"],
                "output_path": raw_path,
                "tokens": tokens,
            }
            if self._voice_ref_path:
                entry["reference"] = self._voice_ref_path
            batch_segments.append(entry)

        r = requests.post(
            f"{self._server_url}/synthesize_batch",
            json={"segments": batch_segments},
            timeout=3600,
        )
        r.raise_for_status()
        batch_results = r.json()["results"]

        # Resample all outputs 24kHz -> 48kHz
        final_results = []
        for br in batch_results:
            raw_path = raw_dir / f"{br['id']}.wav"
            audio, sr = sf.read(str(raw_path), dtype="float32")
            audio = self._resample_to_pipeline(audio, sr)
            final_path = output_dir / f"{br['id']}.wav"
            sf.write(str(final_path), audio, PIPELINE_SAMPLE_RATE)

            final_results.append({
                "segment_id": br["id"],
                "audio_path": str(final_path),
                "duration_ms": br["duration_ms"],
                "sample_rate": PIPELINE_SAMPLE_RATE,
                "inference_s": br["inference_time_s"],
                "tokens_requested": round(
                    next(s["timing_budget_ms"] for s in segments_data
                         if s["segment_id"] == br["id"]) / 80
                ),
            })

        return final_results

    # ------------------------------------------------------------------
    # Subprocess mode (fallback)
    # ------------------------------------------------------------------

    def _synthesize_subprocess(self, text: str, output_path: Path,
                                timing_budget_ms: int | None = None) -> TTSResult:
        """Synthesize via subprocess (slow — loads model each time)."""
        target_tokens = round(timing_budget_ms / 80) if timing_budget_ms else None

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
            env['CUDA_VISIBLE_DEVICES'] = os.environ.get('CUDA_VISIBLE_DEVICES', '1')

            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=300, env=env)

            os.unlink(batch_path)

            if result.returncode != 0:
                logger.error("MOSS-TTS subprocess failed: %s", result.stderr[-500:])
                raise RuntimeError(f"MOSS-TTS inference failed: {result.stderr[-200:]}")

            results = json.load(open(os.path.join(tmpdir, 'moss_results.json')))
            seg_result = results[0]

            audio, sr = sf.read(seg_result['audio_path'], dtype='float32')
            audio = self._resample_to_pipeline(audio, sr)

            if output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                sf.write(str(output_path), audio, PIPELINE_SAMPLE_RATE)

            return TTSResult(
                audio_path=output_path or Path("/dev/null"),
                duration_ms=seg_result['duration_ms'],
                sample_rate=PIPELINE_SAMPLE_RATE,
            )

    def _synthesize_batch_subprocess(self, segments_data: list[dict],
                                      output_dir: Path) -> list[dict]:
        """Batch synthesis via subprocess (original mode)."""
        output_dir.mkdir(parents=True, exist_ok=True)

        batch_path = output_dir / "moss_batch.jsonl"
        with open(batch_path, 'w') as f:
            for seg in segments_data:
                tokens = round(seg['timing_budget_ms'] / 80)
                f.write(json.dumps({
                    'segment_id': seg['segment_id'],
                    'text': seg['text'],
                    'tokens': tokens,
                }) + "\n")

        moss_output_dir = output_dir / "moss_raw"
        moss_output_dir.mkdir(exist_ok=True)

        cmd = [MOSS_VENV_PYTHON, MOSS_INFER_SCRIPT,
               '--batch', str(batch_path), '--output-dir', str(moss_output_dir)]
        if self._voice_ref_path:
            cmd.extend(['--reference', self._voice_ref_path])

        env = os.environ.copy()
        env['CUDA_VISIBLE_DEVICES'] = os.environ.get('CUDA_VISIBLE_DEVICES', '2')

        logger.info("MOSS-TTS subprocess batch: %d segments", len(segments_data))
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=3600, env=env)

        if result.returncode != 0:
            logger.error("MOSS-TTS batch failed: %s", result.stderr[-500:])
            raise RuntimeError(f"MOSS-TTS batch failed: {result.stderr[-200:]}")

        summary = json.loads(result.stdout)
        logger.info("MOSS-TTS batch done: %d segs, %ds total, VRAM %.2f Go",
                     summary['total_segments'], summary['total_inference_s'],
                     summary['vram_peak_gb'])

        moss_results = json.load(open(str(moss_output_dir / "moss_results.json")))
        final_results = []

        for r in moss_results:
            audio, sr = sf.read(r['audio_path'], dtype='float32')
            audio = self._resample_to_pipeline(audio, sr)
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

    # ------------------------------------------------------------------
    # Public API — auto-selects HTTP or subprocess
    # ------------------------------------------------------------------

    def synthesize(self, text: str, voice_profile: Path | None = None,
                   target_lang: str = "en", output_path: Path | None = None,
                   speed: float = 1.0, **kwargs) -> TTSResult:
        """Synthesize a single segment. Uses HTTP server if available."""
        timing_budget_ms = kwargs.get("timing_budget_ms", None)

        if self._http_available:
            try:
                return self._synthesize_http(text, output_path, timing_budget_ms)
            except Exception as exc:
                logger.warning("HTTP synthesis failed (%s), retrying once before fallback", exc)
                try:
                    self._http_available = self._check_server()
                    if self._http_available:
                        return self._synthesize_http(text, output_path, timing_budget_ms)
                except Exception:
                    pass
                logger.warning("HTTP retry also failed, using subprocess")
                self._http_available = False

        return self._synthesize_subprocess(text, output_path, timing_budget_ms)

    def synthesize_batch(self, segments_data: list[dict], output_dir: Path) -> list[dict]:
        """Batch synthesis. Uses HTTP server if available."""
        if self._http_available:
            try:
                return self._synthesize_batch_http(segments_data, output_dir)
            except Exception as exc:
                logger.warning("HTTP batch failed (%s), retrying once before fallback", exc)
                try:
                    self._http_available = self._check_server()
                    if self._http_available:
                        return self._synthesize_batch_http(segments_data, output_dir)
                except Exception:
                    pass
                logger.warning("HTTP batch retry also failed, using subprocess")
                self._http_available = False

        return self._synthesize_batch_subprocess(segments_data, output_dir)

    def unload(self):
        """Nothing to unload — model is in server or subprocess."""
        logger.info("MOSS-TTS engine: nothing to unload (server/subprocess mode)")
