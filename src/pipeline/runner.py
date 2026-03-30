"""Execution sequentielle du pipeline V1 — COMPLET.

Voir MASTERPLAN.md §5.1 — V1 script sequentiel.
Phases: 0-Ingest, 1-Extract, 3-ASR, 4-Segmentation, 6-Translate,
        8-TTS, 9-Assembly, 10-QC, 11-Export.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from src.core.manifest import (
    create_manifest,
    load_manifest,
    save_manifest,
    update_source_metadata,
    update_stage,
)
from src.core.models import Segment, StageStatus
from src.core.segment import build_segments_from_words
from src.core.timing import compute_stretch_ratio, calc_max_chars, classify_timing_fit_text, MAX_SPEED_RATIO, MIN_SLOWDOWN_RATIO
from src.media.ffmpeg import extract_audio, loudnorm, probe_video, remux
from src.media.srt import generate_srt

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_project_id() -> str:
    now = datetime.now(timezone.utc)
    return f"proj_{now.strftime('%Y%m%d_%H%M%S')}"


# =========================================================================
# Phase 0 : Ingest
# =========================================================================


def run_ingest(
    video_path: Path,
    source_lang: str,
    target_langs: list[str],
    data_dir: Path,
    project_id: str | None = None,
) -> tuple[dict, Path]:
    """Phase 0 : Ingest — creer le projet, copier la video, ffprobe."""
    if not video_path.exists():
        raise FileNotFoundError(f"Video source introuvable: {video_path}")

    if project_id is None:
        project_id = generate_project_id()

    project_dir = data_dir / project_id
    source_dir = project_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    dest_video = source_dir / "input.mp4"
    if not dest_video.exists():
        logger.info("Copying video to %s", dest_video)
        shutil.copy2(video_path, dest_video)

    for subdir in ["extracted", "asr", "voice_refs", "tts", "mix", "exports"]:
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    metadata = probe_video(dest_video)

    manifest = create_manifest(
        project_id=project_id,
        source_video=str(dest_video),
        source_lang=source_lang,
        target_langs=target_langs,
    )
    update_source_metadata(manifest, metadata)
    update_stage(manifest, "ingest", StageStatus.COMPLETED)

    manifest_path = project_dir / "manifest.json"
    save_manifest(manifest, manifest_path)

    logger.info("Phase 0 (Ingest) done: project=%s, duration=%dms", project_id, metadata.duration_ms)
    return manifest, manifest_path


# =========================================================================
# Phase 1 : Extract
# =========================================================================


def run_extract(manifest: dict, manifest_path: Path) -> dict:
    """Phase 1 : Extract audio WAV 48kHz mono."""
    project_dir = manifest_path.parent
    source_video = Path(manifest["project"]["source_video"])
    audio_output = project_dir / "extracted" / "audio_48k.wav"

    if audio_output.exists():
        logger.info("Audio already extracted, skipping.")
        update_stage(manifest, "extract", StageStatus.COMPLETED)
        save_manifest(manifest, manifest_path)
        return manifest

    update_stage(manifest, "extract", StageStatus.RUNNING)
    save_manifest(manifest, manifest_path)

    try:
        extract_audio(source_video, audio_output, sample_rate=48000)
        update_stage(manifest, "extract", StageStatus.COMPLETED)
    except Exception as exc:
        update_stage(manifest, "extract", StageStatus.FAILED, error=str(exc))
        save_manifest(manifest, manifest_path)
        raise

    save_manifest(manifest, manifest_path)
    logger.info("Phase 1 (Extract) done: %s", audio_output)
    return manifest




# =========================================================================
# Phase 2 : Demucs (vocal separation)
# =========================================================================


def run_demucs(manifest: dict, manifest_path: Path) -> dict:
    """Phase 2 : Separation vocale via Demucs htdemucs_ft."""
    from src.engines.separation.demucs_engine import DemucsEngine

    project_dir = manifest_path.parent
    extracted_dir = project_dir / "extracted"
    audio_input = extracted_dir / "audio_48k.wav"
    vocals_path = extracted_dir / "vocals.wav"

    if vocals_path.exists():
        logger.info("Vocals already separated, skipping.")
        update_stage(manifest, "demucs", StageStatus.COMPLETED)
        save_manifest(manifest, manifest_path)
        return manifest

    update_stage(manifest, "demucs", StageStatus.RUNNING)
    save_manifest(manifest, manifest_path)

    try:
        engine = DemucsEngine()
        result = engine.separate(audio_input, extracted_dir)
        engine.unload()
        update_stage(manifest, "demucs", StageStatus.COMPLETED)
    except Exception as exc:
        logger.warning("Demucs failed: %s — falling back to original audio", exc)
        update_stage(manifest, "demucs", StageStatus.FAILED, error=str(exc))
        # Fallback: copy original as vocals
        import shutil
        shutil.copy2(str(audio_input), str(vocals_path))

    save_manifest(manifest, manifest_path)
    logger.info("Phase 2 (Demucs) done: %s", vocals_path)
    return manifest

# =========================================================================
# Phase 3 : ASR (WhisperX)
# =========================================================================


def run_asr(manifest: dict, manifest_path: Path, diarize: bool = False, asr_engine: str = "whisperx") -> dict:
    """Phase 3 : ASR — decomposed into 3.1 Transcribe + 3.2 Align + 3.3 Diarize.

    V2: modular pipeline with interchangeable engines (P12).
    """
    project_dir = manifest_path.parent
    vocals_path = project_dir / "extracted" / "vocals.wav"
    audio_path = vocals_path if vocals_path.exists() else project_dir / "extracted" / "audio_48k.wav"
    transcript_path = project_dir / "asr" / "transcript.json"

    if transcript_path.exists():
        logger.info("Transcript already exists, loading.")
        with open(transcript_path) as f:
            data = json.load(f)
        manifest["_words"] = data["words"]
        update_stage(manifest, "asr", StageStatus.COMPLETED, segments_count=len(data["words"]))
        save_manifest(manifest, manifest_path)
        return manifest

    update_stage(manifest, "asr", StageStatus.RUNNING)
    save_manifest(manifest, manifest_path)

    source_lang = manifest["project"]["source_lang"]

    try:
        # Phase 3.1: Transcription
        logger.info("Phase 3.1 — ASR Transcription (%s)", asr_engine)
        if asr_engine == "whisperx":
            from src.engines.asr.whisperx_asr import WhisperXASR
            asr = WhisperXASR()
        elif asr_engine == "qwen3":
            from src.engines.asr.qwen3_asr import Qwen3ASR
            asr = Qwen3ASR()
        elif asr_engine == "voxtral":
            from src.engines.asr.voxtral_asr import VoxtralASR
            asr = VoxtralASR()
        else:
            raise ValueError(f"Unknown ASR engine: {asr_engine}")

        raw_transcript = asr.transcribe(audio_path, language=source_lang)
        asr.unload()

        # Phase 3.2: Alignment
        logger.info("Phase 3.2 — Word-level Alignment")
        from src.engines.asr.whisperx_align import WhisperXAlign
        aligner = WhisperXAlign()
        aligned = aligner.align(raw_transcript, audio_path)

        # Phase 3.3: Diarization (optional)
        if diarize:
            logger.info("Phase 3.3 — Diarization")
            from src.engines.asr.whisperx_diarize import WhisperXDiarize
            diarizer = WhisperXDiarize()
            diarize_result = diarizer.diarize(audio_path, aligned=aligned)
            # aligned.segments is updated in-place by diarizer

        # Convert to word list
        words = []
        for seg in aligned.segments:
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
        logger.info("Phase 3 done: %d words, %d speaker(s)", len(words), n_speakers)

        transcript_data = {
            "language": source_lang,
            "words": words,
            "word_count": len(words),
        }
        with open(transcript_path, "w") as f:
            json.dump(transcript_data, f, indent=2, ensure_ascii=False)

        manifest["_words"] = words
        update_stage(manifest, "asr", StageStatus.COMPLETED, segments_count=len(words))
    except Exception as exc:
        update_stage(manifest, "asr", StageStatus.FAILED, error=str(exc))
        save_manifest(manifest, manifest_path)
        raise

    save_manifest(manifest, manifest_path)
    return manifest


# =========================================================================
# Phase 4 : Segmentation
# =========================================================================


def run_segmentation(manifest: dict, manifest_path: Path) -> dict:
    """Phase 4 : Segmentation logique."""
    project_dir = manifest_path.parent
    segments_path = project_dir / "asr" / "segments.json"

    # Load words
    if "_words" not in manifest:
        transcript_path = project_dir / "asr" / "transcript.json"
        with open(transcript_path) as f:
            manifest["_words"] = json.load(f)["words"]

    if segments_path.exists():
        logger.info("Segments already exist, loading.")
        with open(segments_path) as f:
            seg_data = json.load(f)
        manifest["segments"] = seg_data
        update_stage(manifest, "segmentation", StageStatus.COMPLETED, segments_count=len(seg_data))
        save_manifest(manifest, manifest_path)
        return manifest

    update_stage(manifest, "segmentation", StageStatus.RUNNING)
    save_manifest(manifest, manifest_path)

    try:
        source_lang = manifest["project"]["source_lang"]
        segments = build_segments_from_words(manifest["_words"], source_lang=source_lang)

        seg_data = [dataclasses.asdict(s) for s in segments]
        with open(segments_path, "w") as f:
            json.dump(seg_data, f, indent=2, ensure_ascii=False)

        manifest["segments"] = seg_data
        update_stage(manifest, "segmentation", StageStatus.COMPLETED, segments_count=len(seg_data))
    except Exception as exc:
        update_stage(manifest, "segmentation", StageStatus.FAILED, error=str(exc))
        save_manifest(manifest, manifest_path)
        raise

    save_manifest(manifest, manifest_path)
    logger.info("Phase 4 (Segmentation) done: %d segments", len(seg_data))
    return manifest


# =========================================================================
# Phase 6 : Translation (NLLB-200)
# =========================================================================


def run_translate(manifest: dict, manifest_path: Path, target_lang: str, glossary: dict | None = None, force_reprocess: bool = False) -> dict:
    """Phase 6 : Translate segments via NLLB-200. V2: optional glossary post-processing."""
    from src.engines.mt.nllb_engine import NLLBEngine

    project_dir = manifest_path.parent
    stage_key = f"translate_{target_lang}"
    translations_path = project_dir / "asr" / f"translations_{target_lang}.json"

    if translations_path.exists() and not force_reprocess:
        logger.info("Translations already exist, loading.")
        with open(translations_path) as f:
            manifest[f"_translations_{target_lang}"] = json.load(f)
        update_stage(manifest, stage_key, StageStatus.COMPLETED)
        save_manifest(manifest, manifest_path)
        return manifest
    elif force_reprocess and translations_path.exists():
        logger.info("Force reprocess: ignoring cached translations")

    update_stage(manifest, stage_key, StageStatus.RUNNING)
    save_manifest(manifest, manifest_path)

    models_dir = os.environ.get("SILA_MODELS_DIR", "/opt/sila/models")
    model_path = Path(models_dir) / "nllb-200-3.3b-ct2"

    try:
        engine = NLLBEngine(model_dir=model_path)
        source_lang = manifest["project"]["source_lang"]
        segments = manifest["segments"]

        translations = []
        for i, seg in enumerate(segments):
            text = seg["source_text"]
            result = engine.translate(text, source_lang, target_lang)
            translated = result.text
            glossary_hits = []
            if glossary:
                from src.core.glossary import apply_glossary_post_translation
                translated, glossary_hits = apply_glossary_post_translation(
                    translated, text, glossary, target_lang)

            translations.append({
                "segment_id": seg["segment_id"],
                "source_text": text,
                "translated_text": translated,
                "glossary_hits": glossary_hits if glossary_hits else None,
                "start_ms": seg["start_ms"],
                "end_ms": seg["end_ms"],
                "timing_budget_ms": seg["timing_budget_ms"],
                "estimated_chars": result.estimated_chars,
            })
            if (i + 1) % 10 == 0:
                logger.info("Translated %d/%d segments", i + 1, len(segments))

        engine.unload()

        with open(translations_path, "w") as f:
            json.dump(translations, f, indent=2, ensure_ascii=False)

        manifest[f"_translations_{target_lang}"] = translations
        update_stage(manifest, stage_key, StageStatus.COMPLETED, segments_count=len(translations))
    except Exception as exc:
        update_stage(manifest, stage_key, StageStatus.FAILED, error=str(exc))
        save_manifest(manifest, manifest_path)
        raise

    save_manifest(manifest, manifest_path)
    logger.info("Phase 6 (Translate) done: %d segments -> %s", len(translations), target_lang)
    return manifest



# =========================================================================
# Phase 7 : Rewrite (LLM constrained rewriting)
# =========================================================================


def run_rewrite(manifest: dict, manifest_path: Path, target_lang: str, rewrite_endpoint: str | None = None, glossary: dict | None = None) -> dict:
    """Phase 7 : Reecriture contrainte LLM — qualite-first.

    Reecrit TOUS les segments dont le texte depasse max_chars (REWRITE_NEEDED
    et REVIEW_REQUIRED). Utilise calc_max_chars pour calculer la cible.
    """
    from src.engines.rewrite.llm_rewrite_engine import LLMRewriteEngine
    from src.core.models import TimingFitStatus

    project_dir = manifest_path.parent
    stage_key = f"rewrite_{target_lang}"

    translations_key = f"_translations_{target_lang}"
    if translations_key not in manifest:
        trans_path = project_dir / "asr" / f"translations_{target_lang}.json"
        with open(trans_path) as f:
            manifest[translations_key] = json.load(f)

    translations = manifest[translations_key]
    rewrite_count = 0
    chars_saved = 0

    update_stage(manifest, stage_key, StageStatus.RUNNING)
    save_manifest(manifest, manifest_path)

    try:
        engine_kwargs = {}
        if rewrite_endpoint:
            engine_kwargs["api_base"] = rewrite_endpoint
            logger.info("Using custom rewrite endpoint: %s", rewrite_endpoint)
        engine = LLMRewriteEngine(**engine_kwargs)

        for i, trans in enumerate(translations):
            seg_id = trans["segment_id"]
            text = trans["translated_text"]
            budget_ms = trans["timing_budget_ms"]
            max_chars = calc_max_chars(budget_ms, target_lang)

            # Skip rewrite for short-budget segments (CosyVoice overhead ~3-4s)
            REWRITE_MIN_BUDGET_MS = 7000
            if budget_ms < REWRITE_MIN_BUDGET_MS:
                trans["timing_fit"] = "skip_short_budget"
                trans["rewrite_skipped"] = True
                trans["rewrite_reason"] = "budget_too_short"
                logger.info("Skip rewrite %s: budget %dms < %dms", seg_id, budget_ms, REWRITE_MIN_BUDGET_MS)
                continue

            # Classify using text-based timing fit
            fit = classify_timing_fit_text(text, budget_ms, target_lang)

            if fit == TimingFitStatus.FIT_OK:
                trans["timing_fit"] = "fit_ok"
                trans["rewrite_reason"] = "text_fits"
                continue

            # Rewrite BOTH rewrite_needed AND review_required
            tag = "REWRITE" if fit == TimingFitStatus.REWRITE_NEEDED else "REVIEW+REWRITE"
            logger.info("%s [%d/%d] %s: %d chars -> max %d chars (budget %dms)",
                        tag, i + 1, len(translations), seg_id, len(text), max_chars, budget_ms)

            # Build glossary context for rewrite prompt
            glossary_context = ""
            if glossary:
                from src.core.glossary import build_glossary_prompt_section
                glossary_context = build_glossary_prompt_section(glossary, target_lang, text)

            result = engine.rewrite(
                text=text,
                target_lang=target_lang,
                max_chars=max_chars,
                timing_budget_ms=budget_ms,
                context=glossary_context,
            )

            # Guard: reject empty or absurdly short rewrites (< 20% of original)
            if len(result.text) < max(10, int(len(text) * 0.20)):
                logger.warning("Rewrite %s: LLM returned too-short text (%d chars) — keeping original (%d chars)", seg_id, len(result.text), len(text))
                trans["timing_fit"] = "review_required"
            elif len(result.text) < len(text):
                saved = len(text) - len(result.text)
                chars_saved += saved
                trans["original_text"] = text
                trans["translated_text"] = result.text
                trans["rewritten"] = True
                rewrite_count += 1

                # Reclassify after rewrite
                new_fit = classify_timing_fit_text(result.text, budget_ms, target_lang)
                trans["timing_fit"] = new_fit.value
                logger.info("Rewrite %s: %d -> %d chars (-%d) — %s",
                            seg_id, len(text), len(result.text), saved, new_fit.value)
            else:
                trans["timing_fit"] = "review_required"
                logger.info("Rewrite %s: no reduction (%d -> %d chars)", seg_id, len(text), len(result.text))

        engine.close()

        # Save updated translations
        rewritten_path = project_dir / "asr" / f"translations_{target_lang}.json"
        with open(rewritten_path, "w") as f:
            json.dump(translations, f, indent=2, ensure_ascii=False)

        update_stage(manifest, stage_key, StageStatus.COMPLETED, segments_count=rewrite_count)
    except Exception as exc:
        update_stage(manifest, stage_key, StageStatus.FAILED, error=str(exc))
        save_manifest(manifest, manifest_path)
        raise

    save_manifest(manifest, manifest_path)
    logger.info("Phase 7 (Rewrite) done: %d segments rewritten, %d chars saved", rewrite_count, chars_saved)
    return manifest

# =========================================================================
# Phase 8 : TTS (CosyVoice)
# =========================================================================


def run_tts(manifest: dict, manifest_path: Path, target_lang: str, tts_engine: str = "cosyvoice", diarize_enabled: bool = False, force_reprocess: bool = False) -> dict:
    """Phase 8 : TTS via CosyVoice3 ou Voxtral."""
    if tts_engine == "voxtral":
        from src.engines.tts.voxtral_engine import VoxtralEngine as EngineClass
    else:
        from src.engines.tts.cosyvoice_engine import CosyVoiceEngine as EngineClass

    project_dir = manifest_path.parent
    stage_key = f"tts_{target_lang}"
    tts_dir = project_dir / "tts" / target_lang
    tts_dir.mkdir(parents=True, exist_ok=True)
    tts_manifest_path = tts_dir / "tts_manifest.json"

    # Segment-level resume: load partial TTS manifest if it exists
    _existing_tts = {}
    if tts_manifest_path.exists() and not force_reprocess:
        with open(tts_manifest_path) as f:
            existing = json.load(f)
        _existing_tts = {e["segment_id"]: e for e in existing}
        if len(_existing_tts) >= len(manifest.get("segments", [])):
            logger.info("TTS fully completed (%d segments), loading.", len(_existing_tts))
            manifest[f"_tts_{target_lang}"] = existing
            update_stage(manifest, stage_key, StageStatus.COMPLETED)
            save_manifest(manifest, manifest_path)
            return manifest
        logger.info("TTS partial resume: %d/%d segments already done",
                    len(_existing_tts), len(manifest.get("segments", [])))
    elif force_reprocess and tts_manifest_path.exists():
        logger.info("Force reprocess: ignoring cached TTS")

    update_stage(manifest, stage_key, StageStatus.RUNNING)
    save_manifest(manifest, manifest_path)

    models_dir = os.environ.get("SILA_MODELS_DIR", "/opt/sila/models")
    model_path = Path(models_dir) / "cosyvoice3-0.5b"

    try:
        if tts_engine == "voxtral":
            model_path = Path(models_dir) / "voxtral-tts-4b"
            engine = EngineClass(model_dir=model_path)
        else:
            engine = EngineClass(model_dir=model_path)

        # Build voice reference(s) — per-speaker if diarized (V2)
        vocals_path = project_dir / "extracted" / "vocals.wav"
        audio_path = vocals_path if vocals_path.exists() else project_dir / "extracted" / "audio_48k.wav"
        segments = manifest["segments"]

        # Detect unique speakers
        speakers = sorted(set(s.get("speaker_id", "spk_0") for s in segments))
        speaker_refs = {}

        if diarize_enabled and len(speakers) > 1:
            logger.info("Multi-speaker mode: %d speakers detected: %s", len(speakers), speakers)
            import soundfile as sf
            import numpy as np

            for spk in speakers:
                spk_segments = [s for s in segments if s.get("speaker_id") == spk]
                ref_dir = project_dir / "voice_refs"
                ref_dir.mkdir(parents=True, exist_ok=True)
                ref_path = ref_dir / f"{spk}_multi_ref.wav"

                # Build per-speaker voice reference
                n_ref = engine.set_voice_reference_multi(audio_path, spk_segments, n_best=5, max_duration_s=30.0)
                # Rename the ref file to speaker-specific name
                default_ref = ref_dir / "spk_0_multi_ref.wav"
                if default_ref.exists() and default_ref != ref_path:
                    import shutil
                    shutil.move(str(default_ref), str(ref_path))
                elif not ref_path.exists():
                    # Fallback: use voice_ref.wav if it exists
                    vr = ref_dir / "voice_ref.wav"
                    if vr.exists():
                        import shutil
                        shutil.copy2(str(vr), str(ref_path))

                speaker_refs[spk] = str(ref_path)
                logger.info("Voice ref for %s: %d segments -> %s", spk, n_ref, ref_path)

            # Store speaker refs in manifest
            manifest["speakers"] = {
                spk: {
                    "voice_ref_uri": speaker_refs.get(spk, ""),
                    "segments_used_for_ref": [s["segment_id"] for s in segments if s.get("speaker_id") == spk][:5],
                }
                for spk in speakers
            }
        else:
            # Single speaker (V1 behavior)
            n_ref = engine.set_voice_reference_multi(audio_path, segments, n_best=5, max_duration_s=30.0)
            logger.info("Voice reference: %d segments selected (single speaker)", n_ref)
            speaker_refs["spk_0"] = engine._voice_ref_path

        translations_key = f"_translations_{target_lang}"
        if translations_key not in manifest:
            trans_path = project_dir / "asr" / f"translations_{target_lang}.json"
            with open(trans_path) as f:
                manifest[translations_key] = json.load(f)

        translations = manifest[translations_key]
        tts_outputs = []

        _resume_skipped = 0
        for i, trans in enumerate(translations):
            seg_id = trans["segment_id"]
            text = trans["translated_text"]
            budget_ms = trans["timing_budget_ms"]
            output_path = tts_dir / f"{seg_id}.wav"

            # Segment-level resume: skip already completed segments
            if seg_id in _existing_tts and not force_reprocess:
                tts_outputs.append(_existing_tts[seg_id])
                _resume_skipped += 1
                if _resume_skipped == 1 or _resume_skipped == len(_existing_tts):
                    logger.info("SKIP TTS [%d/%d] %s — already completed (resume)", i + 1, len(translations), seg_id)
                continue

            if _resume_skipped > 0 and _resume_skipped == len(_existing_tts):
                logger.info("Resumed: skipped %d completed segments, processing remaining %d",
                           _resume_skipped, len(translations) - _resume_skipped)
                manifest["resume_info"] = {
                    "total_segments": len(translations),
                    "already_completed": _resume_skipped,
                    "to_process": len(translations) - _resume_skipped,
                    "resumed_at": _now_iso(),
                }

            # V2: switch voice reference per speaker
            if diarize_enabled and len(speaker_refs) > 1:
                # Find speaker for this segment
                seg_speaker = "spk_0"
                for s in segments:
                    if s.get("segment_id") == seg_id:
                        seg_speaker = s.get("speaker_id", "spk_0")
                        break
                ref_path = speaker_refs.get(seg_speaker)
                if ref_path and ref_path != engine._voice_ref_path:
                    engine._voice_ref_path = ref_path
                    logger.info("Switched voice ref to %s for %s", seg_speaker, seg_id)

            # Truncate excessively long text to prevent absurd TTS durations
            max_chars = max(200, int(budget_ms * 0.02))  # ~20 chars/sec
            if len(text) > max_chars:
                logger.warning("TTS text too long for %s (%d chars, max %d) — truncating", seg_id, len(text), max_chars)
                text = text[:max_chars].rsplit(" ", 1)[0]

            # --- TTS: constant speed=1.0, seed=42, optional P2 in [0.95, 1.05] ---
            SEED = 42
            if output_path.exists():
                import soundfile as sf
                info = sf.info(str(output_path))
                tts_result_ms = int(info.duration * 1000)
                tts_speed_used = 1.0
                tts_attempts = 1
                logger.info("TTS [%d/%d] %s: cached (%dms)", i + 1, len(translations), seg_id, tts_result_ms)
            else:
                # Pass 1: always speed=1.0, seed=42
                p1_path = tts_dir / f"{seg_id}_p1.wav"
                logger.info("TTS P1 [%d/%d] %s (speed=1.0, seed=%d): %s", i + 1, len(translations), seg_id, SEED, text[:60])
                tts_result = engine.synthesize(
                    text=text,
                    output_path=p1_path,
                    target_lang=target_lang,
                    speed=1.0,
                )
                p1_ms = tts_result.duration_ms
                tts_attempts = 1

                # Collapse retry (same speed, same seed)
                if p1_ms < budget_ms * 0.10 and budget_ms > 2000:
                    logger.warning("TTS P1 collapsed (%dms) — retrying", p1_ms)
                    tts_result = engine.synthesize(text=text, output_path=p1_path, target_lang=target_lang, speed=1.0)
                    p1_ms = tts_result.duration_ms

                # Check if P1 is close enough (within ±15% or speed adjustment < 1.05)
                ratio = p1_ms / budget_ms if budget_ms > 0 else 1.0

                if 0.85 <= ratio <= 1.15:
                    # P1 fits — keep it
                    import shutil
                    shutil.move(str(p1_path), str(output_path))
                    tts_result_ms = p1_ms
                    tts_speed_used = 1.0
                    logger.info("TTS P1 %s: %dms fits budget %dms (ratio %.2f)", seg_id, p1_ms, budget_ms, ratio)

                elif 0.95 <= ratio <= 1.05:
                    # Very close — keep P1 (stretch will handle it)
                    import shutil
                    shutil.move(str(p1_path), str(output_path))
                    tts_result_ms = p1_ms
                    tts_speed_used = 1.0

                else:
                    # P2: regenerate with speed constrained to [0.95, 1.05]
                    speed_p2 = max(0.95, min(1.05, ratio))
                    p2_path = tts_dir / f"{seg_id}_p2.wav"
                    logger.info("TTS P2 [%d/%d] %s (speed=%.3f, P1 was %dms / %dms)", i + 1, len(translations), seg_id, speed_p2, p1_ms, budget_ms)
                    tts_result = engine.synthesize(text=text, output_path=p2_path, target_lang=target_lang, speed=speed_p2)
                    p2_ms = tts_result.duration_ms
                    tts_attempts = 2

                    # Pick closer to budget
                    if abs(p2_ms - budget_ms) < abs(p1_ms - budget_ms) and p2_ms > budget_ms * 0.10:
                        import shutil
                        shutil.move(str(p2_path), str(output_path))
                        p1_path.unlink(missing_ok=True)
                        tts_result_ms = p2_ms
                        tts_speed_used = speed_p2
                    else:
                        import shutil
                        shutil.move(str(p1_path), str(output_path))
                        p2_path.unlink(missing_ok=True)
                        tts_result_ms = p1_ms
                        tts_speed_used = 1.0

            # --- Time-stretch if needed ---
            stretch_ratio = compute_stretch_ratio(tts_result_ms, budget_ms)
            final_path = output_path
            stretch_applied = False

            if stretch_ratio > 1.01:
                from src.media.rubberband import time_stretch, MAX_STRETCH_RATIO
                adj_path = tts_dir / f"{seg_id}_adj.wav"
                if stretch_ratio <= MAX_STRETCH_RATIO:
                    try:
                        time_stretch(output_path, adj_path, stretch_ratio)
                        final_path = adj_path
                        stretch_applied = True
                    except Exception as e:
                        logger.warning("Stretch failed for %s: %s", seg_id, e)
                else:
                    logger.warning(
                        "Segment %s needs %.2fx stretch (max %.2fx) — review_required",
                        seg_id, stretch_ratio, MAX_STRETCH_RATIO,
                    )

            # --- Slow-down stretch if TTS is too short ---
            slowdown_applied = False
            if tts_result_ms < budget_ms * 0.85 and tts_result_ms > 0:
                slowdown_ratio = tts_result_ms / budget_ms
                if slowdown_ratio >= MIN_SLOWDOWN_RATIO:
                    from src.media.rubberband import time_stretch as rb_stretch, MIN_SLOWDOWN_RATIO as RB_MIN
                    slow_path = tts_dir / f"{seg_id}_slow.wav"
                    try:
                        rb_stretch(final_path, slow_path, slowdown_ratio)
                        final_path = slow_path
                        slowdown_applied = True
                        tts_result_ms = budget_ms  # After slowdown, matches budget
                        logger.info("Slow-down %s: ratio %.3f (%dms -> %dms)", seg_id, slowdown_ratio, tts_result_ms, budget_ms)
                    except Exception as e:
                        logger.warning("Slow-down failed for %s: %s", seg_id, e)

            # Compute TTS overhead metrics
            from src.core.timing import NATURAL_SPEECH_RATES
            debit = NATURAL_SPEECH_RATES.get(target_lang, 10)
            tts_input_chars = len(text)
            theoretical_ms = int((tts_input_chars / debit) * 1000)
            tts_overhead_ms = tts_result_ms - theoretical_ms

            # V2: DNSMOS quality scoring per segment
            dnsmos_score = {}
            try:
                from src.engines.qc.dnsmos_engine import DNSMOSEngine
                if not hasattr(run_tts, '_dnsmos'):
                    run_tts._dnsmos = DNSMOSEngine()
                dnsmos_score = run_tts._dnsmos.score(final_path)
                logger.info("DNSMOS %s: ovrl=%.2f sig=%.2f bak=%.2f p808=%.2f",
                           seg_id, dnsmos_score.get("ovrl_mos", 0),
                           dnsmos_score.get("sig_mos", 0),
                           dnsmos_score.get("bak_mos", 0),
                           dnsmos_score.get("p808_mos", 0))
            except Exception as exc:
                logger.warning("DNSMOS scoring failed for %s: %s", seg_id, exc)

            _tts_entry = {
                "segment_id": seg_id,
                "audio_path": str(final_path),
                "start_ms": trans["start_ms"],
                "end_ms": trans["end_ms"],
                "duration_ms": tts_result_ms,
                "timing_budget_ms": budget_ms,
                "stretch_ratio": round(stretch_ratio, 3),
                "stretch_applied": stretch_applied,
                "slowdown_applied": slowdown_applied,
                "tts_input_chars": tts_input_chars,
                "tts_input_text": text[:200],
                "tts_overhead_ms": tts_overhead_ms,
                "tts_speed_used": round(tts_speed_used, 3) if isinstance(tts_speed_used, float) else 1.0,
                "tts_attempts": tts_attempts if isinstance(tts_attempts, int) else 1,
                "rewrite_reason": trans.get("rewrite_reason"),
                "dnsmos": dnsmos_score,
            }
            tts_outputs.append(_tts_entry)

            # Record TTS segment metric
            try:
                _metrics.record("tts_segment", "done", segment_id=seg_id,
                               duration_ms=tts_result_ms, budget_ms=budget_ms)
            except Exception:
                pass

            # Incremental save: write partial TTS manifest every 5 segments
            if (i + 1) % 5 == 0 or (i + 1) == len(translations):
                with open(tts_manifest_path, "w") as f:
                    json.dump(tts_outputs, f, indent=2, ensure_ascii=False)
                save_manifest(manifest, manifest_path)

        engine.unload()

        with open(tts_manifest_path, "w") as f:
            json.dump(tts_outputs, f, indent=2, ensure_ascii=False)

        manifest[f"_tts_{target_lang}"] = tts_outputs
        update_stage(manifest, stage_key, StageStatus.COMPLETED, segments_count=len(tts_outputs))
    except Exception as exc:
        update_stage(manifest, stage_key, StageStatus.FAILED, error=str(exc))
        save_manifest(manifest, manifest_path)
        raise

    save_manifest(manifest, manifest_path)
    logger.info("Phase 8 (TTS) done: %d segments", len(tts_outputs))
    return manifest


# =========================================================================
# Phase 9 : Assembly
# =========================================================================


def run_assembly(manifest: dict, manifest_path: Path, target_lang: str, demucs_enabled: bool = False, force_reprocess: bool = False) -> dict:
    """Phase 9 : Assembly — place TTS segments on timeline + loudnorm.

    V2: when demucs_enabled, mixes TTS with background audio (accompaniment stems).
    """
    from src.media.assembly import assemble_segments

    project_dir = manifest_path.parent
    stage_key = f"assembly_{target_lang}"
    mix_dir = project_dir / "mix"
    mix_dir.mkdir(parents=True, exist_ok=True)
    mix_raw = mix_dir / f"mix_{target_lang}_raw.wav"
    mix_norm = mix_dir / f"mix_{target_lang}.wav"

    if mix_norm.exists() and not force_reprocess:
        logger.info("Mix already exists, skipping assembly.")
        update_stage(manifest, stage_key, StageStatus.COMPLETED)
        save_manifest(manifest, manifest_path)
        return manifest
    elif force_reprocess and mix_norm.exists():
        logger.info("Force reprocess: regenerating assembly")

    update_stage(manifest, stage_key, StageStatus.RUNNING)
    save_manifest(manifest, manifest_path)

    try:
        tts_key = f"_tts_{target_lang}"
        if tts_key not in manifest:
            tts_dir = project_dir / "tts" / target_lang / "tts_manifest.json"
            with open(tts_dir) as f:
                manifest[tts_key] = json.load(f)

        tts_outputs = manifest[tts_key]
        total_duration_ms = manifest["project"]["duration_ms"]

        # V2: pass background audio (Demucs accompaniment) if available
        background_audio_path = None
        if demucs_enabled:
            accomp_path = project_dir / "extracted" / "accompaniment.wav"
            if accomp_path.exists():
                background_audio_path = accomp_path
                logger.info("Background audio found: %s", accomp_path)
            else:
                logger.warning("Demucs enabled but no accompaniment.wav found — mixing without background")

        assemble_segments(
            segments=tts_outputs,
            output_path=mix_raw,
            total_duration_ms=total_duration_ms,
            background_audio_path=background_audio_path,
        )

        # Loudnorm
        loudnorm(mix_raw, mix_norm, target_lufs=-16.0)

        update_stage(manifest, stage_key, StageStatus.COMPLETED)
    except Exception as exc:
        update_stage(manifest, stage_key, StageStatus.FAILED, error=str(exc))
        save_manifest(manifest, manifest_path)
        raise

    save_manifest(manifest, manifest_path)
    logger.info("Phase 9 (Assembly) done: %s", mix_norm)
    return manifest


# =========================================================================
# Phase 10 : QC
# =========================================================================


def run_qc(manifest: dict, manifest_path: Path, target_lang: str) -> dict:
    """Phase 10 : QC — timing checks."""
    from src.engines.qc.basic_qc import BasicQCEngine

    project_dir = manifest_path.parent
    stage_key = f"qc_{target_lang}"
    qc_report_path = project_dir / "qc_report.json"

    update_stage(manifest, stage_key, StageStatus.RUNNING)
    save_manifest(manifest, manifest_path)

    try:
        engine = BasicQCEngine()
        tts_key = f"_tts_{target_lang}"
        if tts_key not in manifest:
            tts_path = project_dir / "tts" / target_lang / "tts_manifest.json"
            with open(tts_path) as f:
                manifest[tts_key] = json.load(f)

        tts_outputs = manifest[tts_key]
        segments_qc = []

        for tts_out in tts_outputs:
            audio_path = Path(tts_out["audio_path"])
            result = engine.check(audio_path, tts_out["timing_budget_ms"])
            seg_qc = {
                "segment_id": tts_out["segment_id"],
                "budget_ms": tts_out["timing_budget_ms"],
                "actual_ms": result.duration_ms,
                "delta_ms": result.timing_delta_ms,
                "flags": result.flags,
            }
            # V2: pass DNSMOS scores through to QC report
            if "dnsmos" in tts_out:
                seg_qc["dnsmos"] = tts_out["dnsmos"]
            segments_qc.append(seg_qc)

        # Mix-level checks (loudness, true peak, duration)
        mix_path = project_dir / "mix" / f"mix_{target_lang}.wav"
        mix_checks = None
        if mix_path.exists():
            mix_checks = engine.check_mix(mix_path, manifest["project"]["duration_ms"])

        report = engine.generate_report(segments_qc, qc_report_path, mix_checks=mix_checks)
        update_stage(manifest, stage_key, StageStatus.COMPLETED)
    except Exception as exc:
        update_stage(manifest, stage_key, StageStatus.FAILED, error=str(exc))
        save_manifest(manifest, manifest_path)
        raise

    save_manifest(manifest, manifest_path)
    logger.info("Phase 10 (QC) done: %.0f%% pass rate", report["pass_rate"] * 100)
    return manifest


# =========================================================================
# Phase 11 : Export
# =========================================================================


def run_export(manifest: dict, manifest_path: Path, target_lang: str, multitrack: bool = False) -> dict:
    """Phase 11 : Export — SRT + remux MP4. V3: optional multi-track export."""
    project_dir = manifest_path.parent
    stage_key = f"export_{target_lang}"
    exports_dir = project_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    update_stage(manifest, stage_key, StageStatus.RUNNING)
    save_manifest(manifest, manifest_path)

    try:
        # Generate SRT
        trans_key = f"_translations_{target_lang}"
        if trans_key not in manifest:
            trans_path = project_dir / "asr" / f"translations_{target_lang}.json"
            with open(trans_path) as f:
                manifest[trans_key] = json.load(f)

        srt_path = exports_dir / f"{target_lang}.srt"
        generate_srt(manifest[trans_key], srt_path, text_key="translated_text")

        # Remux video + dubbed audio
        source_video = Path(manifest["project"]["source_video"])
        mix_audio = project_dir / "mix" / f"mix_{target_lang}.wav"
        output_video = exports_dir / f"output_{target_lang}.mp4"

        remux(source_video, mix_audio, output_video, target_lang=target_lang)

        # V3: Multi-track export (separate voice + background stems)
        if multitrack:
            from src.media.ffmpeg import remux_multitrack
            mix_dir = project_dir / "mix"
            voice_only = mix_dir / f"mix_{target_lang}_voice_only.wav"
            background = mix_dir / f"mix_{target_lang}_background.wav"
            if voice_only.exists():
                # Also export individual WAV stems
                import shutil
                shutil.copy2(str(voice_only), str(exports_dir / f"{target_lang}_voice.wav"))
                if background.exists():
                    shutil.copy2(str(background), str(exports_dir / f"{target_lang}_background.wav"))
                # Multi-track MP4
                mt_video = exports_dir / f"output_{target_lang}_multitrack.mp4"
                remux_multitrack(
                    source_video, mix_audio, voice_only,
                    background if background.exists() else None,
                    mt_video, target_lang=target_lang,
                )
                manifest["outputs"][target_lang]["multitrack_video_uri"] = str(mt_video)
                manifest["outputs"][target_lang]["voice_wav_uri"] = str(exports_dir / f"{target_lang}_voice.wav")
                if background.exists():
                    manifest["outputs"][target_lang]["background_wav_uri"] = str(exports_dir / f"{target_lang}_background.wav")
                logger.info("Multi-track export: %s", mt_video)
            else:
                logger.warning("Voice-only WAV not found — skipping multitrack export")

        # Update outputs in manifest
        manifest["outputs"][target_lang] = {
            "status": "completed",
            "video_uri": str(output_video),
            "srt_uri": str(srt_path),
            "audio_mix_uri": str(mix_audio),
        }
        manifest["project"]["status"] = "completed"
        manifest["metrics"]["processing_finished_at"] = _now_iso()

        update_stage(manifest, stage_key, StageStatus.COMPLETED)
    except Exception as exc:
        update_stage(manifest, stage_key, StageStatus.FAILED, error=str(exc))
        save_manifest(manifest, manifest_path)
        raise

    save_manifest(manifest, manifest_path)
    logger.info("Phase 11 (Export) done: %s", output_video)
    return manifest


# =========================================================================
# Pipeline complet
# =========================================================================


def run_pipeline(
    video_path: Path,
    source_lang: str,
    target_lang: str,
    data_dir: Path | None = None,
    project_id: str | None = None,
    from_stage: str | None = None,
    tts_engine: str = "cosyvoice",
    demucs_enabled: bool = False,
    demucs_auto: bool = False,
    diarize_enabled: bool = False,
    rewrite_endpoint: str | None = None,
    glossary_path: str | None = None,
    asr_engine: str = "whisperx",
    force_reprocess: bool = False,
    multitrack: bool = False,
    target_langs: list[str] | None = None,
) -> dict:
    """Execute le pipeline V1 complet (sequentiel).

    Phases: 0-Ingest, 1-Extract, 3-ASR, 4-Segmentation, 6-Translate,
            8-TTS, 9-Assembly, 10-QC, 11-Export.
    """
    if data_dir is None:
        data_dir = Path("data/projects")

    if not target_langs:
        target_langs = [target_lang]
    t0 = time.time()
    from src.monitoring.metrics import PipelineMetrics
    _metrics = type('NullMetrics', (), {'record': lambda *a, **k: None, 'summary': lambda s: {}})()

    # Phase 0 : Ingest
    logger.info("=" * 60)
    logger.info("PHASE 0 — INGEST")
    logger.info("=" * 60)
    manifest, manifest_path = run_ingest(
        video_path=video_path,
        source_lang=source_lang,
        target_langs=target_langs,
        data_dir=data_dir,
        project_id=project_id,
    )
    _metrics = PipelineMetrics(manifest_path.parent)
    _metrics.record("pipeline", "start")

    # Phase 1 : Extract
    logger.info("=" * 60)
    logger.info("PHASE 1 — EXTRACT")
    logger.info("=" * 60)
    manifest = run_extract(manifest, manifest_path)

    # Phase 2 : Demucs (optional, off by default — ADR-008)
    # V2: auto-detection via SNR analysis
    if demucs_auto:
        from src.media.snr_detect import detect_background_audio
        audio_48k = manifest_path.parent / "extracted" / "audio_48k.wav"
        logger.info("=" * 60)
        logger.info("PHASE 2a — SNR DETECTION (auto Demucs)")
        logger.info("=" * 60)
        snr_result = detect_background_audio(str(audio_48k))
        manifest["snr_detection"] = snr_result
        save_manifest(manifest, manifest_path)
        demucs_enabled = snr_result["has_background"]
        logger.info("SNR auto-detection: ratio=%.4f -> %s (demucs=%s)",
                    snr_result["background_ratio"], snr_result["recommendation"], demucs_enabled)

    if demucs_enabled:
        logger.info("=" * 60)
        logger.info("PHASE 2 — DEMUCS (vocal separation)")
        logger.info("=" * 60)
        t_demucs = time.time()
        manifest = run_demucs(manifest, manifest_path)
        logger.info("Demucs took %.1fs", time.time() - t_demucs)
    else:
        logger.info("Demucs disabled (default). Use --demucs for videos with background music.")

    # Phase 3 : ASR
    logger.info("=" * 60)
    logger.info("PHASE 3 — ASR (WhisperX)")
    logger.info("=" * 60)
    t_asr = time.time()
    manifest = run_asr(manifest, manifest_path, diarize=diarize_enabled, asr_engine=asr_engine)
    logger.info("ASR took %.1fs", time.time() - t_asr)
    _metrics.record("asr", "end", duration_s=round(time.time() - t_asr, 1))

    # Phase 4 : Segmentation
    logger.info("=" * 60)
    logger.info("PHASE 4 — SEGMENTATION")
    logger.info("=" * 60)
    manifest = run_segmentation(manifest, manifest_path)

    # === Fan-out: Phases 6-11 per target language ===
    # Load glossary if provided
    _glossary = None
    if glossary_path:
        from src.core.glossary import load_glossary
        _glossary = load_glossary(glossary_path)

    all_langs = target_langs if target_langs else [target_lang]
    for lang_idx, lang in enumerate(all_langs):
        logger.info("=" * 60)
        logger.info("LANGUAGE %d/%d — %s", lang_idx + 1, len(all_langs), lang.upper())
        logger.info("=" * 60)

        # Phase 6 : Translation
        logger.info("PHASE 6 — TRANSLATION (NLLB-200) [%s]", lang)
        t_mt = time.time()
        manifest = run_translate(manifest, manifest_path, lang, glossary=_glossary, force_reprocess=force_reprocess)
        logger.info("Translation [%s] took %.1fs", lang, time.time() - t_mt)
        _metrics.record("translate", "end", lang=lang, duration_s=round(time.time() - t_mt, 1))

        # Phase 7 : Rewrite (LLM constrained)
        logger.info("PHASE 7 — REWRITE (LLM) [%s]", lang)
        t_rw = time.time()
        manifest = run_rewrite(manifest, manifest_path, lang, rewrite_endpoint=rewrite_endpoint, glossary=_glossary)
        logger.info("Rewrite [%s] took %.1fs", lang, time.time() - t_rw)
        _metrics.record("rewrite", "end", lang=lang, duration_s=round(time.time() - t_rw, 1))

        # Phase 8 : TTS
        logger.info("PHASE 8 — TTS (CosyVoice3) [%s]", lang)
        t_tts = time.time()
        manifest = run_tts(manifest, manifest_path, lang, tts_engine=tts_engine, diarize_enabled=diarize_enabled, force_reprocess=force_reprocess)
        logger.info("TTS [%s] took %.1fs", lang, time.time() - t_tts)
        _metrics.record("tts", "end", lang=lang, duration_s=round(time.time() - t_tts, 1))

        # Phase 9 : Assembly
        logger.info("PHASE 9 — ASSEMBLY [%s]", lang)
        manifest = run_assembly(manifest, manifest_path, lang, demucs_enabled=demucs_enabled, force_reprocess=force_reprocess)

        # Phase 10 : QC
        logger.info("PHASE 10 — QC [%s]", lang)
        manifest = run_qc(manifest, manifest_path, lang)

        # Phase 11 : Export
        logger.info("PHASE 11 — EXPORT [%s]", lang)
        manifest = run_export(manifest, manifest_path, lang, multitrack=multitrack)

    total_time = time.time() - t0
    manifest["metrics"]["total_processing_time_s"] = round(total_time, 1)
    _metrics.record("pipeline", "end", total_s=round(total_time, 1))
    manifest["pipeline_metrics"] = _metrics.summary()
    save_manifest(manifest, manifest_path)

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE — %.1fs total", total_time)
    logger.info("=" * 60)
    return manifest
