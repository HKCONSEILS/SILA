"""SRT subtitle generation — Phase 11.

Voir MASTERPLAN.md §6.1 Phase 11.2 — generer SRT.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def ms_to_srt_time(ms: int) -> str:
    """Convert milliseconds to SRT timecode HH:MM:SS,mmm."""
    hours = ms // 3_600_000
    ms %= 3_600_000
    minutes = ms // 60_000
    ms %= 60_000
    seconds = ms // 1_000
    millis = ms % 1_000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def generate_srt(
    segments: list[dict],
    output_path: Path,
    text_key: str = "translated_text",
) -> Path:
    """Genere un fichier SRT a partir des segments traduits.

    Args:
        segments: Liste de segments avec start_ms, end_ms, et text_key.
        output_path: Chemin du fichier SRT.
        text_key: Cle contenant le texte a afficher.

    Returns:
        Chemin du fichier SRT.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, seg in enumerate(segments, 1):
        start = ms_to_srt_time(seg["start_ms"])
        end = ms_to_srt_time(seg["end_ms"])
        text = seg.get(text_key, seg.get("source_text", ""))
        lines.append(f"{i}")
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("SRT generated: %s (%d subtitles)", output_path, len(segments))
    return output_path
