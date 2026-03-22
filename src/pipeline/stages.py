"""Enum des etapes du pipeline et transitions autorisees.

Voir MASTERPLAN.md §6.1 pour le DAG complet.
"""

from __future__ import annotations

import enum


class Stage(str, enum.Enum):
    """Etapes du pipeline V1. Voir MASTERPLAN.md §6.1."""

    INGEST = "ingest"
    EXTRACT = "extract"
    DEMUCS = "demucs"
    ASR = "asr"
    SEGMENTATION = "segmentation"
    CONTEXT = "context"
    TRANSLATE = "translate"
    TTS = "tts"
    ASSEMBLY = "assembly"
    QC = "qc"
    EXPORT = "export"


# Etapes V1 dans l'ordre d'execution (Demucs skipped)
V1_STAGE_ORDER: list[Stage] = [
    Stage.INGEST,
    Stage.EXTRACT,
    # Stage.DEMUCS — skipped en V1 (ADR-005)
    Stage.ASR,
    Stage.SEGMENTATION,
    # Stage.CONTEXT — simplifie en V1
    Stage.TRANSLATE,
    Stage.TTS,
    Stage.ASSEMBLY,
    Stage.QC,
    Stage.EXPORT,
]

# Etapes du tronc commun (executees une seule fois). Voir MASTERPLAN.md §2.1 P3.
TRUNK_STAGES: set[Stage] = {
    Stage.INGEST,
    Stage.EXTRACT,
    Stage.DEMUCS,
    Stage.ASR,
    Stage.SEGMENTATION,
    Stage.CONTEXT,
}

# Etapes par-langue (fan-out)
PER_LANG_STAGES: set[Stage] = {
    Stage.TRANSLATE,
    Stage.TTS,
    Stage.ASSEMBLY,
    Stage.QC,
    Stage.EXPORT,
}
