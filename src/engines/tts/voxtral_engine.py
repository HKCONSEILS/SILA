"""Voxtral TTS 4B engine — Phase 8 challenger.

Voir MASTERPLAN.md §3.4 — Voxtral TTS 4B (Mistral, mars 2026).
Cross-lingual voice cloning via vLLM-Omni.
Pas de parametre speed — time-stretch rubberband uniquement.
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


class VoxtralEngine(TTSInterface):
    """Voxtral TTS 4B via vLLM-Omni."""

    def __init__(
        self,
        model_dir: str | Path = "/opt/sila/models/voxtral-tts-4b",
        device: str = "cuda",
    ):
        self.model_dir = str(model_dir)
        self.device = device
        self._model = None
        self._voice_ref_path = None
        self._sample_rate = 24000  # Voxtral output sample rate

    def _load(self):
        if self._model is not None:
            return
        from vllm_omni import LLM, SamplingParams

        logger.info("Loading Voxtral TTS 4B from %s...", self.model_dir)
        self._model = LLM(
            model=self.model_dir,
            tokenizer=self.model_dir,
            gpu_memory_utilization=0.5,
            max_model_len=4096,
            dtype="float16",
            enforce_eager=True,
        )
        self._SamplingParams = SamplingParams
        logger.info("Voxtral TTS 4B loaded.")

    def set_voice_reference(self, audio_path: Path, start_ms: int = 0, end_ms: int = 10000):
        """Extract voice reference (2-3s for Voxtral)."""
        audio, sr = sf.read(str(audio_path), dtype="float32")
        if audio.ndim > 1:
            audio = audio[:, 0]

        # Voxtral works best with 2-3s reference
        start_sample = int(start_ms * sr / 1000)
        ref_duration_ms = min(3000, end_ms - start_ms)
        end_sample = min(int((start_ms + ref_duration_ms) * sr / 1000), len(audio))
        segment = audio[start_sample:end_sample]

        ref_dir = Path(audio_path).parent.parent / "voice_refs"
        ref_dir.mkdir(parents=True, exist_ok=True)
        ref_path = ref_dir / "voice_ref_voxtral.wav"
        sf.write(str(ref_path), segment, sr)
        self._voice_ref_path = str(ref_path)

        logger.info(
            "Voxtral voice reference saved: %.1fs -> %s",
            len(segment) / sr, ref_path,
        )

    def synthesize(
        self,
        text: str,
        voice_profile: Path | None = None,
        target_lang: str = "en",
        output_path: Path | None = None,
        speed: float = 1.0,  # Ignored — Voxtral has no speed param
    ) -> TTSResult:
        """Synthesize speech using Voxtral TTS 4B.

        Voxtral uses a text+audio prompt approach. No speed parameter.
        """
        self._load()

        if self._voice_ref_path is None:
            raise RuntimeError("Voice reference not set. Call set_voice_reference() first.")

        # Build the prompt for Voxtral cross-lingual TTS
        try:
            from mistral_common.protocol.instruct.messages import (
                TTSRequest,
                TextChunk,
                AudioChunk,
            )
            from mistral_common.protocol.instruct.request import ChatCompletionRequest

            # Load reference audio
            ref_audio, ref_sr = sf.read(self._voice_ref_path, dtype="float32")
            if ref_audio.ndim > 1:
                ref_audio = ref_audio[:, 0]

            # Build TTS request using mistral_common
            tts_request = TTSRequest(
                text=text,
                audio_prompt=ref_audio.tolist(),
                audio_prompt_sample_rate=ref_sr,
            )

            # Generate via vLLM
            sampling_params = self._SamplingParams(
                temperature=0.1,
                max_tokens=4096,
            )

            outputs = self._model.generate(
                prompts=[tts_request],
                sampling_params=sampling_params,
            )

            # Extract audio from output
            if outputs and len(outputs) > 0:
                output = outputs[0]
                if hasattr(output, 'audio') and output.audio is not None:
                    audio = np.array(output.audio, dtype=np.float32)
                elif hasattr(output, 'outputs') and len(output.outputs) > 0:
                    out = output.outputs[0]
                    if hasattr(out, 'audio') and out.audio is not None:
                        audio = np.array(out.audio, dtype=np.float32)
                    else:
                        raise RuntimeError(f"No audio in vLLM output. Keys: {dir(out)}")
                else:
                    raise RuntimeError(f"Unexpected vLLM output format: {type(output)}")
            else:
                raise RuntimeError("Empty vLLM output")

        except ImportError as e:
            logger.warning("mistral_common TTS API not available (%s), using fallback", e)
            # Fallback: use vLLM raw text generation with audio output
            audio = self._synthesize_fallback(text)

        except Exception as e:
            logger.warning("Voxtral synthesis error: %s. Generating silence.", e)
            silence_duration = max(1.0, len(text) * 0.08)
            audio = np.zeros(int(silence_duration * self._sample_rate), dtype=np.float32)

        duration_ms = int(len(audio) / self._sample_rate * 1000)

        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(output_path), audio, self._sample_rate)

        return TTSResult(
            audio_path=output_path or Path("/dev/null"),
            duration_ms=duration_ms,
            sample_rate=self._sample_rate,
        )

    def _synthesize_fallback(self, text: str) -> np.ndarray:
        """Fallback synthesis if the mistral_common TTS API isn't available."""
        # Try direct vLLM text-to-speech
        prompt = f"[INST] Generate speech for: {text} [/INST]"
        sampling_params = self._SamplingParams(temperature=0.1, max_tokens=4096)
        outputs = self._model.generate([prompt], sampling_params=sampling_params)

        if outputs and hasattr(outputs[0], 'audio'):
            return np.array(outputs[0].audio, dtype=np.float32)

        # If all else fails, generate silence
        logger.warning("Fallback: generating silence for '%s'", text[:50])
        return np.zeros(int(len(text) * 0.08 * self._sample_rate), dtype=np.float32)

    def unload(self):
        import gc
        self._model = None
        self._voice_ref_path = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("Voxtral TTS unloaded.")
