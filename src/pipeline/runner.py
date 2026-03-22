"""Execution sequentielle du pipeline V1.

Voir MASTERPLAN.md §5.1 — V1 script sequentiel.
Voir CLAUDE.md §Workflow V1 pour l'ordre d'implementation.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from src.core.manifest import (
    create_manifest,
    load_manifest,
    save_manifest,
    update_source_metadata,
    update_stage,
)
from src.core.models import StageStatus
from src.media.ffmpeg import extract_audio, probe_video

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_project_id() -> str:
    """Genere un project_id deterministe. Voir MASTERPLAN.md §8.1."""
    now = datetime.now(timezone.utc)
    return f"proj_{now.strftime('%Y%m%d_%H%M%S')}"


def run_ingest(
    video_path: Path,
    source_lang: str,
    target_langs: list[str],
    data_dir: Path,
    project_id: str | None = None,
) -> tuple[dict, Path]:
    """Phase 0 : Ingest — creer le projet, copier la video, ffprobe, ecrire le manifeste.

    Voir MASTERPLAN.md §6.1 Phase 0.

    Args:
        video_path: Chemin vers la video source.
        source_lang: Code ISO 639-1 de la langue source.
        target_langs: Liste des langues cibles.
        data_dir: Repertoire racine des donnees (data/projects/).
        project_id: ID du projet (genere si non fourni).

    Returns:
        Tuple (manifeste, chemin du manifeste).
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video source introuvable: {video_path}")

    if project_id is None:
        project_id = generate_project_id()

    project_dir = data_dir / project_id
    source_dir = project_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    # Copier la video source
    dest_video = source_dir / "input.mp4"
    if not dest_video.exists():
        logger.info("Copying video to %s", dest_video)
        shutil.copy2(video_path, dest_video)
    else:
        logger.info("Video already present: %s", dest_video)

    # Creer les repertoires du projet. Voir MASTERPLAN.md §11.3.
    for subdir in ["extracted", "asr", "voice_refs", "tts", "mix", "exports"]:
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    # ffprobe pour les metadonnees
    logger.info("Probing video metadata...")
    metadata = probe_video(dest_video)

    # Creer le manifeste initial
    manifest = create_manifest(
        project_id=project_id,
        source_video=str(dest_video),
        source_lang=source_lang,
        target_langs=target_langs,
    )
    update_source_metadata(manifest, metadata)
    update_stage(manifest, "ingest", StageStatus.COMPLETED)

    # Sauvegarder
    manifest_path = project_dir / "manifest.json"
    save_manifest(manifest, manifest_path)

    logger.info(
        "Ingest completed: project=%s, duration=%dms, resolution=%s",
        project_id,
        metadata.duration_ms,
        metadata.resolution,
    )
    return manifest, manifest_path


def run_extract(manifest: dict, manifest_path: Path) -> dict:
    """Phase 1 : Extraction — FFmpeg -> WAV 48kHz mono + metadonnees video.

    Voir MASTERPLAN.md §6.1 Phase 1.
    Principe P5 : timebase 48 kHz.
    Principe P11 : idempotence — skip si la sortie existe.

    Args:
        manifest: Manifeste courant.
        manifest_path: Chemin du manifeste sur disque.

    Returns:
        Manifeste mis a jour.
    """
    project_dir = manifest_path.parent
    source_video = Path(manifest["project"]["source_video"])

    # Chemin de sortie. Voir MASTERPLAN.md §11.3.
    audio_output = project_dir / "extracted" / "audio_48k.wav"

    # Idempotence (P11) : skip si l'artefact existe deja
    if audio_output.exists():
        logger.info("Audio already extracted, skipping: %s", audio_output)
        update_stage(manifest, "extract", StageStatus.COMPLETED)
        save_manifest(manifest, manifest_path)
        return manifest

    update_stage(manifest, "extract", StageStatus.RUNNING)
    save_manifest(manifest, manifest_path)

    try:
        extract_audio(source_video, audio_output, sample_rate=48000)
        update_stage(manifest, "extract", StageStatus.COMPLETED)
    except Exception as exc:
        logger.error("Extraction failed: %s", exc)
        update_stage(manifest, "extract", StageStatus.FAILED, error=str(exc))
        save_manifest(manifest, manifest_path)
        raise

    save_manifest(manifest, manifest_path)
    logger.info("Extraction completed: %s", audio_output)
    return manifest


def run_pipeline(
    video_path: Path,
    source_lang: str,
    target_lang: str,
    data_dir: Path | None = None,
    project_id: str | None = None,
    from_stage: str | None = None,
) -> dict:
    """Execute le pipeline V1 complet (sequentiel).

    Voir MASTERPLAN.md §5.1 — V1 script sequentiel.
    Voir CLAUDE.md §Workflow V1 pour l'ordre.

    Args:
        video_path: Chemin vers la video source.
        source_lang: Langue source (ISO 639-1).
        target_lang: Langue cible (ISO 639-1).
        data_dir: Repertoire des donnees (defaut: data/projects/).
        project_id: ID du projet (genere si non fourni).
        from_stage: Reprendre depuis cette etape (pour relance).

    Returns:
        Manifeste final.
    """
    if data_dir is None:
        data_dir = Path("data/projects")

    target_langs = [target_lang]

    # Phase 0 : Ingest
    manifest, manifest_path = run_ingest(
        video_path=video_path,
        source_lang=source_lang,
        target_langs=target_langs,
        data_dir=data_dir,
        project_id=project_id,
    )

    # Phase 1 : Extraction
    manifest = run_extract(manifest, manifest_path)

    # Phases suivantes : a implementer
    # Phase 3 : ASR (WhisperX)
    # Phase 4 : Segmentation
    # Phase 6 : Traduction
    # Phase 8 : TTS
    # Phase 9 : Assembly
    # Phase 10 : QC
    # Phase 11 : Export

    logger.info("Pipeline V1 completed phases 0-1 for project %s", manifest["project"]["project_id"])
    return manifest
