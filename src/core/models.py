"""Contrats de donnees du pipeline SILA.

Voir MASTERPLAN.md §7 pour les schemas complets.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class StageStatus(str, enum.Enum):
    """Statut d'une etape du pipeline. Voir MASTERPLAN.md §8."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TimingFitStatus(str, enum.Enum):
    """Statut d'adéquation temporelle. Voir MASTERPLAN.md §6.3."""

    FIT_OK = "fit_ok"
    REWRITE_NEEDED = "rewrite_needed"
    REVIEW_REQUIRED = "review_required"


class SegmentType(str, enum.Enum):
    """Type de segment logique. Voir MASTERPLAN.md §7.2."""

    SPEECH = "speech"
    OVERLAP = "overlap"
    SILENCE = "silence"
    MUSIC = "music"


# ---------------------------------------------------------------------------
# §7.1 — Transcript canonique (sortie Phase 3)
# ---------------------------------------------------------------------------


@dataclass
class Word:
    """Un mot transcrit avec timestamps. Voir MASTERPLAN.md §7.1."""

    word_id: int
    chunk_id: int
    speaker_id: str
    source_lang: str
    start_ms: int
    end_ms: int
    text: str
    confidence: float
    is_overlap: bool = False
    sentence_id: int = 0


# ---------------------------------------------------------------------------
# §7.2 — Segments logiques (sortie Phase 4)
# ---------------------------------------------------------------------------


@dataclass
class Segment:
    """Segment logique du pipeline. Voir MASTERPLAN.md §7.2."""

    segment_id: str
    speaker_id: str
    start_ms: int
    end_ms: int
    duration_ms: int
    timing_budget_ms: int
    source_text: str
    source_lang: str
    context_left: str = ""
    context_right: str = ""
    segment_type: SegmentType = SegmentType.SPEECH
    words: list[dict[str, Any]] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# §7.3 — Traductions (sortie Phase 6)
# ---------------------------------------------------------------------------


@dataclass
class TranslationResult:
    """Resultat de traduction d'un segment. Voir MASTERPLAN.md §7.3."""

    segment_id: str
    target_lang: str
    translated_text: str
    alt_text_short: str | None = None
    estimated_chars: int = 0
    estimated_duration_ms: int = 0
    compression_ratio: float = 1.0
    timing_fit_status: TimingFitStatus = TimingFitStatus.FIT_OK
    glossary_hits: list[str] = field(default_factory=list)
    mt_engine: str = ""
    mt_model_version: str = ""


# ---------------------------------------------------------------------------
# §7.4 — Sorties TTS (sortie Phase 8)
# ---------------------------------------------------------------------------


@dataclass
class TTSOutput:
    """Resultat de synthese vocale d'un segment. Voir MASTERPLAN.md §7.4."""

    segment_id: str
    target_lang: str
    voice_profile_id: str = ""
    tts_engine: str = ""
    tts_model_version: str = ""
    audio_uri: str = ""
    duration_ms: int = 0
    timing_budget_ms: int = 0
    timing_delta_ms: int = 0
    stretch_applied: bool = False
    stretch_ratio: float = 1.0
    final_audio_uri: str = ""
    seed: int = 42
    utmos_score: float | None = None


# ---------------------------------------------------------------------------
# Metadata video source
# ---------------------------------------------------------------------------


@dataclass
class SourceMetadata:
    """Metadonnees de la video source. Voir MASTERPLAN.md §8.2."""

    fps: float = 0.0
    resolution: str = ""
    codec_video: str = ""
    codec_audio: str = ""
    sample_rate: int = 48000
    duration_ms: int = 0
    chapters: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage info pour le manifeste
# ---------------------------------------------------------------------------


@dataclass
class StageInfo:
    """Info d'avancement d'une etape. Voir MASTERPLAN.md §8.2."""

    status: StageStatus = StageStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    reason: str | None = None
    segments_count: int | None = None
    segments_done: int | None = None
    segments_total: int | None = None
    error: str | None = None
