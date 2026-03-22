"""Definition du DAG du pipeline. Voir MASTERPLAN.md §6.1."""

from __future__ import annotations

from src.pipeline.stages import Stage

# Dependances directes entre etapes V1
DEPENDENCIES: dict[Stage, list[Stage]] = {
    Stage.INGEST: [],
    Stage.EXTRACT: [Stage.INGEST],
    Stage.ASR: [Stage.EXTRACT],
    Stage.SEGMENTATION: [Stage.ASR],
    Stage.TRANSLATE: [Stage.SEGMENTATION],
    Stage.TTS: [Stage.TRANSLATE],
    Stage.ASSEMBLY: [Stage.TTS],
    Stage.QC: [Stage.ASSEMBLY],
    Stage.EXPORT: [Stage.QC],
}
