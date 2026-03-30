"""WhisperX ASR engine — Phase 3.

Voir MASTERPLAN.md §3.1 — WhisperX (faster-whisper backend).
V2: optional diarization via pyannote.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from src.engines.asr.interface import ASRInterface, TranscriptResult

logger = logging.getLogger(__name__)


class WhisperXEngine(ASRInterface):
    """WhisperX ASR avec word-level timestamps."""

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "float16",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return
        import whisperx
        logger.info("Loading WhisperX model %s on %s (%s)...", self.model_size, self.device, self.compute_type)
        self._model = whisperx.load_model(
            self.model_size,
            self.device,
            compute_type=self.compute_type,
        )
        logger.info("WhisperX model loaded.")

    def transcribe(self, audio_path: Path, language: str = "fr", diarize: bool = False) -> TranscriptResult:
        """Transcrit un fichier audio avec WhisperX.

        Args:
            audio_path: Path to audio file.
            language: Source language code.
            diarize: If True, run pyannote diarization and assign speaker IDs.
        """
        import whisperx

        self._load_model()

        logger.info("Transcribing %s (lang=%s, diarize=%s)...", audio_path, language, diarize)
        audio = whisperx.load_audio(str(audio_path))
        result = self._model.transcribe(audio, batch_size=16, language=language)

        # Alignement word-level
        logger.info("Aligning transcription...")
        model_a, metadata = whisperx.load_align_model(language_code=language, device=self.device)
        result = whisperx.align(
            result["segments"],
            model_a,
            metadata,
            audio,
            self.device,
            return_char_alignments=False,
        )
        del model_a

        # V2: Diarization
        speaker_map = {}
        if diarize:
            speaker_map = self._run_diarization(audio, result)

        # Convertir en format interne
        words = []
        for seg in result.get("segments", []):
            seg_speaker = seg.get("speaker", "spk_0")
            for w in seg.get("words", []):
                if "start" not in w or "end" not in w:
                    continue
                words.append({
                    "text": w["word"].strip(),
                    "start_ms": int(w["start"] * 1000),
                    "end_ms": int(w["end"] * 1000),
                    "confidence": w.get("score", 0.0),
                    "speaker": w.get("speaker", seg_speaker),
                })

        n_speakers = len(set(w.get("speaker", "spk_0") for w in words))
        logger.info("Transcription done: %d words, %d speaker(s)", len(words), n_speakers)
        return TranscriptResult(words=words, language=language)

    def _run_diarization(self, audio, aligned_result: dict) -> dict:
        """Run pyannote diarization and assign speakers to segments.

        Returns dict mapping segment index to speaker ID.
        """
        import whisperx

        hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        if not hf_token:
            # Check token file
            token_path = Path.home() / ".huggingface" / "token"
            if token_path.exists():
                hf_token = token_path.read_text().strip()

        if not hf_token:
            logger.warning("No HuggingFace token found. Diarization requires accepting pyannote conditions. "
                         "Set HF_TOKEN env var or create ~/.huggingface/token. "
                         "Falling back to single-speaker mode.")
            return {}

        try:
            logger.info("Loading pyannote diarization pipeline...")
            diarize_model = whisperx.DiarizationPipeline(
                use_auth_token=hf_token,
                device=self.device,
            )
            logger.info("Running diarization...")
            diarize_segments = diarize_model(audio)
            result = whisperx.assign_word_speakers(diarize_segments, aligned_result)

            # Count speakers
            speakers = set()
            for seg in result.get("segments", []):
                if "speaker" in seg:
                    speakers.add(seg["speaker"])
                for w in seg.get("words", []):
                    if "speaker" in w:
                        speakers.add(w["speaker"])

            logger.info("Diarization done: %d speakers detected: %s", len(speakers), sorted(speakers))

            # Update aligned_result in place (whisperx.assign_word_speakers does this)
            return {s: s for s in speakers}

        except Exception as exc:
            logger.warning("Diarization failed: %s — falling back to single-speaker", exc)
            return {}

    def unload(self):
        """Libere la VRAM."""
        import gc, torch
        self._model = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("WhisperX model unloaded.")
