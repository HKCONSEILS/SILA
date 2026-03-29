"""Segmentation logique — regles metier.

Voir MASTERPLAN.md §6.2 pour les 8 regles de segmentation.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.models import Segment, SegmentType

logger = logging.getLogger(__name__)

# Constantes de segmentation. Voir MASTERPLAN.md §6.2.
MIN_SEGMENT_DURATION_MS = 3000     # S5 : minimum 3s
NOMINAL_MIN_DURATION_MS = 4000     # S3 : duree nominale 4-8s
NOMINAL_MAX_DURATION_MS = 8000     # S3
HARD_CAP_DURATION_MS = 10000       # S4 : hard cap 10s
PAUSE_SPLIT_THRESHOLD_MS = 400     # S2 : pause > 400ms
PAUSE_FORCE_SPLIT_MS = 200         # S4 : forcer coupe sur pause > 200ms
STRONG_PUNCTUATION = ".?!"         # S2 : ponctuation forte
WEAK_PUNCTUATION = ","             # S4 : ponctuation faible
PHRASE_SEARCH_THRESHOLD_MS = 9000  # seuil recherche phrase (etait NOMINAL_MAX=8000)
MIN_BUDGET_EFFECTIVE_MS = 6000     # plancher CosyVoice (overhead ~3-4s)


def build_segments_from_words(
    words: list[dict[str, Any]],
    speaker_id: str = "spk_0",
    source_lang: str = "fr",
) -> list[Segment]:
    """Construit les segments logiques a partir des mots transcrits.

    Applique les 8 regles de segmentation de MASTERPLAN.md §6.2 :
    - S1 : pas de melange de locuteurs (V1 = 1 locuteur)
    - S2 : couper sur pause + ponctuation forte
    - S3 : duree nominale 4-8s
    - S4 : hard cap 10s, forcer coupe
    - S5 : minimum 3s, fusionner si trop court
    - S6 : seuil adaptatif (simplifie en V1)
    - S7 : overlaps (V1 = pas de diarisation)
    - S8 : contexte conserve (context_left, context_right)

    Args:
        words: Liste de mots avec 'text', 'start_ms', 'end_ms', 'confidence'.
        speaker_id: ID du locuteur (V1 = "spk_0").
        source_lang: Langue source.

    Returns:
        Liste de Segments logiques.
    """
    if not words:
        return []

    segments: list[Segment] = []
    current_words: list[dict[str, Any]] = []
    current_start_ms = words[0].get("start_ms", 0)

    def _flush_segment() -> None:
        if not current_words:
            return
        start = current_words[0].get("start_ms", 0)
        end = current_words[-1].get("end_ms", 0)
        duration = end - start
        text = " ".join(w.get("text", "") for w in current_words)
        seg_index = len(segments) + 1
        segments.append(Segment(
            segment_id=f"seg_{seg_index:04d}",
            speaker_id=speaker_id,
            start_ms=start,
            end_ms=end,
            duration_ms=duration,
            timing_budget_ms=duration,
            source_text=text,
            source_lang=source_lang,
            segment_type=SegmentType.SPEECH,
            words=list(current_words),
        ))

    for i, word in enumerate(words):
        current_words.append(word)
        word_end = word.get("end_ms", 0)
        current_duration = word_end - current_start_ms
        text_so_far = word.get("text", "")

        # PHRASE-AWARE: if approaching hard cap, look back for sentence boundary
        import os
        phrase_aware_enabled = os.environ.get("SILA_NO_PHRASE_AWARE", "0") != "1"
        if phrase_aware_enabled and current_duration >= PHRASE_SEARCH_THRESHOLD_MS and len(current_words) > 1:
            # Search backwards for a word ending with strong punctuation
            best_cut = -1
            min_duration_for_cut = current_start_ms + NOMINAL_MIN_DURATION_MS
            for j in range(len(current_words) - 1, 0, -1):
                w = current_words[j]
                w_text = w.get("text", "").rstrip()
                w_end = w.get("end_ms", 0)
                if w_end < min_duration_for_cut:
                    break  # Don't go below 4s
                if any(w_text.endswith(p) for p in (".?!;:")):
                    best_cut = j
                    break
            if best_cut > 0:
                # Guard: don't cut if resulting segment < MIN_BUDGET_EFFECTIVE_MS
                cut_end = current_words[best_cut].get("end_ms", 0)
                resulting_duration = cut_end - current_start_ms
                if resulting_duration < MIN_BUDGET_EFFECTIVE_MS:
                    best_cut = -1  # Cancel cut, let segment grow
            if best_cut > 0:
                # Cut at the sentence boundary
                kept = current_words[:best_cut + 1]
                leftover = current_words[best_cut + 1:]
                current_words = kept
                _flush_segment()
                current_words = leftover
                if leftover:
                    current_start_ms = leftover[0].get("start_ms", 0)
                elif i + 1 < len(words):
                    current_start_ms = words[i + 1].get("start_ms", 0)
                continue

        # S4 : hard cap — forcer la coupe
        if current_duration >= HARD_CAP_DURATION_MS:
            _flush_segment()
            current_words = []
            if i + 1 < len(words):
                current_start_ms = words[i + 1].get("start_ms", 0)
            continue

        # Verifier s'il y a une pause avant le mot suivant
        if i + 1 < len(words):
            next_start = words[i + 1].get("start_ms", 0)
            gap_ms = next_start - word_end

            # S2 : couper sur pause + ponctuation forte si duree nominale atteinte
            ends_strong = any(text_so_far.rstrip().endswith(p) for p in STRONG_PUNCTUATION)
            ends_weak = any(text_so_far.rstrip().endswith(p) for p in WEAK_PUNCTUATION)

            if current_duration >= NOMINAL_MIN_DURATION_MS:
                if gap_ms >= PAUSE_SPLIT_THRESHOLD_MS and ends_strong:
                    _flush_segment()
                    current_words = []
                    current_start_ms = next_start
                    continue
                # S4 : si on approche du hard cap, couper sur ponctuation faible
                if current_duration >= NOMINAL_MAX_DURATION_MS:
                    if gap_ms >= PAUSE_FORCE_SPLIT_MS and (ends_strong or ends_weak):
                        _flush_segment()
                        current_words = []
                        current_start_ms = next_start
                        continue

    # Flush remaining
    _flush_segment()

    # S5 : fusionner les segments trop courts avec le precedent
    segments = _merge_short_segments(segments)

    # S8 : ajouter context_left et context_right
    _add_context(segments)

    return segments


def _merge_short_segments(segments: list[Segment]) -> list[Segment]:
    """Fusionne les segments < 3s avec le segment adjacent. Voir MASTERPLAN.md §6.2 S5."""
    if len(segments) <= 1:
        return segments

    merged: list[Segment] = [segments[0]]
    for seg in segments[1:]:
        if seg.duration_ms < MIN_BUDGET_EFFECTIVE_MS and merged:
            prev = merged[-1]
            # Guard: don't merge if combined would exceed hard cap
            if prev.duration_ms + seg.duration_ms > HARD_CAP_DURATION_MS:
                merged.append(seg)
                continue
            merged[-1] = Segment(
                segment_id=prev.segment_id,
                speaker_id=prev.speaker_id,
                start_ms=prev.start_ms,
                end_ms=seg.end_ms,
                duration_ms=seg.end_ms - prev.start_ms,
                timing_budget_ms=seg.end_ms - prev.start_ms,
                source_text=f"{prev.source_text} {seg.source_text}",
                source_lang=prev.source_lang,
                segment_type=prev.segment_type,
                words=prev.words + seg.words,
                review_flags=prev.review_flags + seg.review_flags,
            )
        else:
            merged.append(seg)

    # Re-index
    for i, seg in enumerate(merged):
        seg.segment_id = f"seg_{i + 1:04d}"

    return merged


def _add_context(segments: list[Segment]) -> None:
    """Ajoute context_left et context_right. Voir MASTERPLAN.md §6.2 S8."""
    for i, seg in enumerate(segments):
        # context_left : 2-3 segments precedents
        left_texts = [segments[j].source_text for j in range(max(0, i - 3), i)]
        seg.context_left = " ".join(left_texts)

        # context_right : 1-2 segments suivants
        right_texts = [segments[j].source_text for j in range(i + 1, min(len(segments), i + 3))]
        seg.context_right = " ".join(right_texts)
