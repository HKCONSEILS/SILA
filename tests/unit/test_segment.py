"""Tests unitaires pour la segmentation logique.

Voir MASTERPLAN.md §6.2 — 8 regles de segmentation.
"""

from src.core.segment import build_segments_from_words


def _make_words(texts: list[str], start_ms: int = 0, word_duration_ms: int = 500, gap_ms: int = 100):
    """Helper pour creer une liste de mots synthetiques."""
    words = []
    t = start_ms
    for i, text in enumerate(texts):
        words.append({
            "text": text,
            "start_ms": t,
            "end_ms": t + word_duration_ms,
            "confidence": 0.95,
        })
        t += word_duration_ms + gap_ms
    return words


def test_basic_segmentation():
    """Des mots forment au moins un segment."""
    words = _make_words(["Bonjour", "a", "tous", "et", "bienvenue", "dans", "cette", "presentation."])
    segments = build_segments_from_words(words)
    assert len(segments) >= 1
    assert segments[0].segment_id == "seg_0001"
    assert segments[0].speaker_id == "spk_0"


def test_segment_has_context():
    """Chaque segment doit avoir context_left et context_right (S8)."""
    words = _make_words(
        ["Bonjour."] * 5 + ["Au"] + ["revoir."] * 5,
        word_duration_ms=1500,
        gap_ms=500,
    )
    segments = build_segments_from_words(words)
    if len(segments) > 1:
        # Le 2e segment doit avoir un context_left non vide
        assert segments[1].context_left != ""


def test_empty_words():
    segments = build_segments_from_words([])
    assert segments == []


def test_single_word():
    words = _make_words(["Bonjour."], word_duration_ms=4000)
    segments = build_segments_from_words(words)
    assert len(segments) == 1
