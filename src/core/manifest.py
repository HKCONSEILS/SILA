"""Lecture / ecriture / validation du manifeste central.

Voir MASTERPLAN.md §8 pour la structure complete.
Le manifeste JSON est la source de verite du run (principe P4).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.models import SourceMetadata, StageInfo, StageStatus

logger = logging.getLogger(__name__)

PIPELINE_VERSION = "0.1.0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_manifest(
    project_id: str,
    source_video: str,
    source_lang: str,
    target_langs: list[str],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cree un manifeste initial vide. Voir MASTERPLAN.md §8.2."""
    now = _now_iso()
    default_config = {
        "tts_engine": "cosyvoice-3.0-0.5b",
        "mt_engine": "nllb-200-3.3b-ct2",
        "max_segment_duration_ms": 10000,
        "preferred_segment_duration_ms": 6000,
        "pause_split_threshold_ms": 400,
        "crossfade_ms": 50,
        "max_stretch_ratio": 1.25,
        "loudness_target_lufs": -16,
        "tts_seed": 42,
    }
    if config:
        default_config.update(config)

    stages: dict[str, Any] = {
        "ingest": {"status": StageStatus.PENDING.value},
        "extract": {"status": StageStatus.PENDING.value},
        "demucs": {"status": StageStatus.SKIPPED.value, "reason": "V1 — pas de separation vocale"},
        "asr": {"status": StageStatus.PENDING.value},
        "segmentation": {"status": StageStatus.PENDING.value},
        "context": {"status": StageStatus.SKIPPED.value, "reason": "V1 — simplifie"},
    }
    for lang in target_langs:
        stages[f"translate_{lang}"] = {"status": StageStatus.PENDING.value}
        stages[f"tts_{lang}"] = {"status": StageStatus.PENDING.value}
        stages[f"assembly_{lang}"] = {"status": StageStatus.PENDING.value}
        stages[f"qc_{lang}"] = {"status": StageStatus.PENDING.value}
        stages[f"export_{lang}"] = {"status": StageStatus.PENDING.value}

    outputs: dict[str, Any] = {}
    for lang in target_langs:
        outputs[lang] = {
            "status": "pending",
            "audio_mix_uri": None,
            "video_uri": None,
            "srt_uri": None,
            "segments_done": 0,
            "segments_total": 0,
        }

    return {
        "manifest_version": 1,
        "pipeline_version": PIPELINE_VERSION,
        "created_at": now,
        "updated_at": now,
        "project": {
            "project_id": project_id,
            "status": "processing",
            "source_video": source_video,
            "source_lang": source_lang,
            "target_langs": target_langs,
            "duration_ms": 0,
        },
        "source_metadata": {
            "fps": 0.0,
            "resolution": "",
            "codec_video": "",
            "codec_audio": "",
            "sample_rate": 48000,
            "chapters": [],
        },
        "config": default_config,
        "speakers": {},
        "stages": stages,
        "segments": [],
        "outputs": outputs,
        "metrics": {
            "processing_started_at": now,
            "processing_finished_at": None,
            "total_processing_time_s": None,
            "gpu_time_s": None,
        },
    }


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    """Charge le manifeste depuis le disque."""
    logger.debug("Loading manifest from %s", manifest_path)
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


def save_manifest(manifest: dict[str, Any], manifest_path: Path) -> None:
    """Sauvegarde le manifeste sur disque. Voir MASTERPLAN.md §8.1 — artefacts immuables (P13)."""
    manifest["updated_at"] = _now_iso()
    manifest["manifest_version"] = manifest.get("manifest_version", 0) + 1
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    logger.info("Manifest saved to %s (version %d)", manifest_path, manifest["manifest_version"])


def update_stage(
    manifest: dict[str, Any],
    stage_key: str,
    status: StageStatus,
    **extra: Any,
) -> None:
    """Met a jour le statut d'une etape dans le manifeste."""
    now = _now_iso()
    stage = manifest["stages"].setdefault(stage_key, {})
    stage["status"] = status.value

    if status == StageStatus.RUNNING:
        stage["started_at"] = now
    elif status in (StageStatus.COMPLETED, StageStatus.FAILED):
        stage["finished_at"] = now

    stage.update(extra)
    manifest["updated_at"] = now


def update_source_metadata(manifest: dict[str, Any], metadata: SourceMetadata) -> None:
    """Met a jour les metadonnees source dans le manifeste."""
    manifest["source_metadata"] = {
        "fps": metadata.fps,
        "resolution": metadata.resolution,
        "codec_video": metadata.codec_video,
        "codec_audio": metadata.codec_audio,
        "sample_rate": metadata.sample_rate,
        "chapters": metadata.chapters,
    }
    if metadata.duration_ms:
        manifest["project"]["duration_ms"] = metadata.duration_ms


def compute_artifact_hash(file_path: Path) -> str:
    """Calcule le hash SHA-256 d'un artefact. Voir MASTERPLAN.md §12.1."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()
