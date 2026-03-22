"""Tests unitaires pour le manifeste central.

Voir MASTERPLAN.md §8 pour la structure du manifeste.
"""

import json
from pathlib import Path

from src.core.manifest import (
    create_manifest,
    load_manifest,
    save_manifest,
    update_stage,
)
from src.core.models import StageStatus


def test_create_manifest():
    manifest = create_manifest(
        project_id="proj_test_001",
        source_video="data/projects/proj_test_001/source/input.mp4",
        source_lang="fr",
        target_langs=["en"],
    )
    assert manifest["project"]["project_id"] == "proj_test_001"
    assert manifest["project"]["source_lang"] == "fr"
    assert manifest["project"]["target_langs"] == ["en"]
    assert manifest["config"]["tts_engine"] == "cosyvoice-3.0-0.5b"
    assert manifest["config"]["loudness_target_lufs"] == -16
    assert manifest["config"]["crossfade_ms"] == 50
    assert manifest["config"]["max_stretch_ratio"] == 1.25
    assert manifest["stages"]["demucs"]["status"] == "skipped"
    assert manifest["stages"]["translate_en"]["status"] == "pending"


def test_update_stage():
    manifest = create_manifest(
        project_id="proj_test",
        source_video="test.mp4",
        source_lang="fr",
        target_langs=["en"],
    )
    update_stage(manifest, "ingest", StageStatus.RUNNING)
    assert manifest["stages"]["ingest"]["status"] == "running"
    assert "started_at" in manifest["stages"]["ingest"]

    update_stage(manifest, "ingest", StageStatus.COMPLETED)
    assert manifest["stages"]["ingest"]["status"] == "completed"
    assert "finished_at" in manifest["stages"]["ingest"]


def test_save_and_load_manifest(tmp_path: Path):
    manifest = create_manifest(
        project_id="proj_test",
        source_video="test.mp4",
        source_lang="fr",
        target_langs=["en"],
    )
    manifest_path = tmp_path / "manifest.json"
    save_manifest(manifest, manifest_path)
    assert manifest_path.exists()

    loaded = load_manifest(manifest_path)
    assert loaded["project"]["project_id"] == "proj_test"
    assert loaded["manifest_version"] >= 1
