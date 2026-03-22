"""Tests unitaires pour les modeles de donnees.

Voir MASTERPLAN.md §7 pour les contrats de donnees.
"""

from src.core.models import (
    Segment,
    SegmentType,
    SourceMetadata,
    StageInfo,
    StageStatus,
    TimingFitStatus,
    Word,
)


def test_word_creation():
    word = Word(
        word_id=1,
        chunk_id=0,
        speaker_id="spk_0",
        source_lang="fr",
        start_ms=1200,
        end_ms=1680,
        text="Bonjour",
        confidence=0.97,
    )
    assert word.text == "Bonjour"
    assert word.start_ms == 1200
    assert word.is_overlap is False


def test_segment_creation():
    seg = Segment(
        segment_id="seg_0001",
        speaker_id="spk_0",
        start_ms=1200,
        end_ms=7650,
        duration_ms=6450,
        timing_budget_ms=6450,
        source_text="Bonjour a tous.",
        source_lang="fr",
    )
    assert seg.segment_id == "seg_0001"
    assert seg.duration_ms == 6450
    assert seg.segment_type == SegmentType.SPEECH
    assert seg.review_flags == []


def test_source_metadata():
    meta = SourceMetadata(
        fps=29.97,
        resolution="1920x1080",
        codec_video="h264",
        codec_audio="aac",
        sample_rate=48000,
        duration_ms=600000,
    )
    assert meta.fps == 29.97
    assert meta.sample_rate == 48000


def test_stage_status_enum():
    assert StageStatus.PENDING.value == "pending"
    assert StageStatus.COMPLETED.value == "completed"


def test_timing_fit_status_enum():
    assert TimingFitStatus.FIT_OK.value == "fit_ok"
    assert TimingFitStatus.REWRITE_NEEDED.value == "rewrite_needed"
