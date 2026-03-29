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


def run_asr(manifest: dict, manifest_path: Path) -> dict:
    """Phase 3 : ASR via WhisperX."""
    from src.engines.asr.whisperx_engine import WhisperXEngine

    project_dir = manifest_path.parent
    # Use vocals.wav (Demucs output) if available, else original audio
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

    try:
        engine = WhisperXEngine()
        source_lang = manifest["project"]["source_lang"]
        result = engine.transcribe(audio_path, language=source_lang)
        engine.unload()

        transcript_data = {
            "language": result.language,
            "words": result.words,
            "word_count": len(result.words),
        }
        with open(transcript_path, "w") as f:
            json.dump(transcript_data, f, indent=2, ensure_ascii=False)

        manifest["_words"] = result.words
        update_stage(manifest, "asr", StageStatus.COMPLETED, segments_count=len(result.words))
    except Exception as exc:
        update_stage(manifest, "asr", StageStatus.FAILED, error=str(exc))
        save_manifest(manifest, manifest_path)
        raise

    save_manifest(manifest, manifest_path)
    logger.info("Phase 3 (ASR) done: %d words", len(result.words))
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


def run_translate(manifest: dict, manifest_path: Path, target_lang: str) -> dict:
    """Phase 6 : Translate segments via NLLB-200."""
    from src.engines.mt.nllb_engine import NLLBEngine

    project_dir = manifest_path.parent
    stage_key = f"translate_{target_lang}"
    translations_path = project_dir / "asr" / f"translations_{target_lang}.json"

    if translations_path.exists():
        logger.info("Translations already exist, loading.")
        with open(translations_path) as f:
            manifest[f"_translations_{target_lang}"] = json.load(f)
        update_stage(manifest, stage_key, StageStatus.COMPLETED)
        save_manifest(manifest, manifest_path)
        return manifest

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
            translations.append({
                "segment_id": seg["segment_id"],
                "source_text": text,
                "translated_text": result.text,
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


def run_rewrite(manifest: dict, manifest_path: Path, target_lang: str) -> dict:
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
        engine = LLMRewriteEngine()

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

            result = engine.rewrite(
                text=text,
                target_lang=target_lang,
                max_chars=max_chars,
                timing_budget_ms=budget_ms,
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


def run_tts(manifest: dict, manifest_path: Path, target_lang: str, tts_engine: str = "cosyvoice") -> dict:
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

    if tts_manifest_path.exists():
        logger.info("TTS already done, loading manifest.")
        with open(tts_manifest_path) as f:
            manifest[f"_tts_{target_lang}"] = json.load(f)
        update_stage(manifest, stage_key, StageStatus.COMPLETED)
        save_manifest(manifest, manifest_path)
        return manifest

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

        # Build multi-segment voice reference (P6 masterplan)
        vocals_path = project_dir / "extracted" / "vocals.wav"
        audio_path = vocals_path if vocals_path.exists() else project_dir / "extracted" / "audio_48k.wav"
        segments = manifest["segments"]
        n_ref = engine.set_voice_reference_multi(audio_path, segments, n_best=5, max_duration_s=30.0)
        logger.info("Voice reference: %d segments selected", n_ref)

        translations_key = f"_translations_{target_lang}"
        if translations_key not in manifest:
            trans_path = project_dir / "asr" / f"translations_{target_lang}.json"
            with open(trans_path) as f:
                manifest[translations_key] = json.load(f)

        translations = manifest[translations_key]
        tts_outputs = []

        for i, trans in enumerate(translations):
            seg_id = trans["segment_id"]
            text = trans["translated_text"]
            budget_ms = trans["timing_budget_ms"]
            output_path = tts_dir / f"{seg_id}.wav"

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

            tts_outputs.append({
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
            })

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


def run_assembly(manifest: dict, manifest_path: Path, target_lang: str) -> dict:
    """Phase 9 : Assembly — place TTS segments on timeline + loudnorm."""
    from src.media.assembly import assemble_segments

    project_dir = manifest_path.parent
    stage_key = f"assembly_{target_lang}"
    mix_dir = project_dir / "mix"
    mix_dir.mkdir(parents=True, exist_ok=True)
    mix_raw = mix_dir / f"mix_{target_lang}_raw.wav"
    mix_norm = mix_dir / f"mix_{target_lang}.wav"

    if mix_norm.exists():
        logger.info("Mix already exists, skipping assembly.")
        update_stage(manifest, stage_key, StageStatus.COMPLETED)
        save_manifest(manifest, manifest_path)
        return manifest

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

        assemble_segments(
            segments=tts_outputs,
            output_path=mix_raw,
            total_duration_ms=total_duration_ms,
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
            segments_qc.append({
                "segment_id": tts_out["segment_id"],
                "budget_ms": tts_out["timing_budget_ms"],
                "actual_ms": result.duration_ms,
                "delta_ms": result.timing_delta_ms,
                "flags": result.flags,
            })

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


def run_export(manifest: dict, manifest_path: Path, target_lang: str) -> dict:
    """Phase 11 : Export — SRT + remux MP4."""
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
) -> dict:
    """Execute le pipeline V1 complet (sequentiel).

    Phases: 0-Ingest, 1-Extract, 3-ASR, 4-Segmentation, 6-Translate,
            8-TTS, 9-Assembly, 10-QC, 11-Export.
    """
    if data_dir is None:
        data_dir = Path("data/projects")

    target_langs = [target_lang]
    t0 = time.time()

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

    # Phase 1 : Extract
    logger.info("=" * 60)
    logger.info("PHASE 1 — EXTRACT")
    logger.info("=" * 60)
    manifest = run_extract(manifest, manifest_path)

    # Phase 2 : Demucs (optional, off by default — ADR-008)
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
    manifest = run_asr(manifest, manifest_path)
    logger.info("ASR took %.1fs", time.time() - t_asr)

    # Phase 4 : Segmentation
    logger.info("=" * 60)
    logger.info("PHASE 4 — SEGMENTATION")
    logger.info("=" * 60)
    manifest = run_segmentation(manifest, manifest_path)

    # Phase 6 : Translation
    logger.info("=" * 60)
    logger.info("PHASE 6 — TRANSLATION (NLLB-200)")
    logger.info("=" * 60)
    t_mt = time.time()
    manifest = run_translate(manifest, manifest_path, target_lang)
    logger.info("Translation took %.1fs", time.time() - t_mt)

    # Phase 7 : Rewrite (LLM constrained)
    logger.info("=" * 60)
    logger.info("PHASE 7 — REWRITE (LLM)")
    logger.info("=" * 60)
    t_rw = time.time()
    manifest = run_rewrite(manifest, manifest_path, target_lang)
    logger.info("Rewrite took %.1fs", time.time() - t_rw)

    # Phase 8 : TTS
    logger.info("=" * 60)
    logger.info("PHASE 8 — TTS (CosyVoice3)")
    logger.info("=" * 60)
    t_tts = time.time()
    manifest = run_tts(manifest, manifest_path, target_lang, tts_engine=tts_engine)
    logger.info("TTS took %.1fs", time.time() - t_tts)

    # Phase 9 : Assembly
    logger.info("=" * 60)
    logger.info("PHASE 9 — ASSEMBLY")
    logger.info("=" * 60)
    manifest = run_assembly(manifest, manifest_path, target_lang)

    # Phase 10 : QC
    logger.info("=" * 60)
    logger.info("PHASE 10 — QC")
    logger.info("=" * 60)
    manifest = run_qc(manifest, manifest_path, target_lang)

    # Phase 11 : Export
    logger.info("=" * 60)
    logger.info("PHASE 11 — EXPORT")
    logger.info("=" * 60)
    manifest = run_export(manifest, manifest_path, target_lang)

    total_time = time.time() - t0
    manifest["metrics"]["total_processing_time_s"] = round(total_time, 1)
    save_manifest(manifest, manifest_path)

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE — %.1fs total", total_time)
    logger.info("=" * 60)
    return manifest
