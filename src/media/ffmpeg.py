"""Wrappers FFmpeg pour extraction audio, metadonnees, remux et loudnorm.

Voir MASTERPLAN.md §3.1 — FFmpeg 6+ pour extraction/remux.
Voir MASTERPLAN.md §11.3 — conventions de nommage des artefacts.
Principe P5 : timebase 48 kHz. Principe P14 : video source intouchee.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from src.core.models import SourceMetadata

logger = logging.getLogger(__name__)


def _run_ffmpeg(args: list[str], description: str) -> subprocess.CompletedProcess[str]:
    """Execute une commande FFmpeg avec gestion d'erreurs."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg {description} failed: {result.stderr.strip()}")
    return result


def _run_ffprobe(args: list[str]) -> str:
    """Execute une commande ffprobe et retourne stdout."""
    cmd = ["ffprobe", "-hide_banner", *args]
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    return result.stdout


def probe_video(video_path: Path) -> SourceMetadata:
    """Extrait les metadonnees d'une video avec ffprobe. Voir MASTERPLAN.md §6.1 Phase 0."""
    raw = _run_ffprobe([
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        str(video_path),
    ])
    data = json.loads(raw)

    video_stream = None
    audio_stream = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and video_stream is None:
            video_stream = stream
        elif stream.get("codec_type") == "audio" and audio_stream is None:
            audio_stream = stream

    fps = 0.0
    resolution = ""
    codec_video = ""
    if video_stream:
        # Parse fps from r_frame_rate (e.g. "30000/1001")
        r_frame_rate = video_stream.get("r_frame_rate", "0/1")
        if "/" in r_frame_rate:
            num, den = r_frame_rate.split("/")
            fps = float(num) / float(den) if float(den) else 0.0
        else:
            fps = float(r_frame_rate)
        w = video_stream.get("width", 0)
        h = video_stream.get("height", 0)
        resolution = f"{w}x{h}"
        codec_video = video_stream.get("codec_name", "")

    codec_audio = ""
    sample_rate = 48000
    if audio_stream:
        codec_audio = audio_stream.get("codec_name", "")
        sample_rate = int(audio_stream.get("sample_rate", 48000))

    duration_s = float(data.get("format", {}).get("duration", 0))
    duration_ms = int(duration_s * 1000)

    chapters = []
    for ch in data.get("chapters", []):
        chapters.append({
            "id": ch.get("id", 0),
            "start_time": float(ch.get("start_time", 0)),
            "end_time": float(ch.get("end_time", 0)),
            "title": ch.get("tags", {}).get("title", ""),
        })

    return SourceMetadata(
        fps=round(fps, 3),
        resolution=resolution,
        codec_video=codec_video,
        codec_audio=codec_audio,
        sample_rate=sample_rate,
        duration_ms=duration_ms,
        chapters=chapters,
    )


def extract_audio(
    video_path: Path,
    output_path: Path,
    sample_rate: int = 48000,
) -> Path:
    """Extrait l'audio d'une video en WAV mono.

    Voir MASTERPLAN.md §6.1 Phase 1.1 — FFmpeg -> WAV 48kHz mono.
    Principe P5 : timebase 48 kHz.

    Args:
        video_path: Chemin vers la video source.
        output_path: Chemin de sortie pour le WAV.
        sample_rate: Frequence d'echantillonnage (48000 par defaut).

    Returns:
        Chemin vers le fichier WAV extrait.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(sample_rate),
            "-ac", "1",
            str(output_path),
        ],
        description="extract audio",
    )
    logger.info("Audio extracted: %s (%d Hz mono)", output_path, sample_rate)
    return output_path


def loudnorm(
    input_path: Path,
    output_path: Path,
    target_lufs: float = -16.0,
) -> Path:
    """Normalise le loudness d'un fichier audio.

    Voir MASTERPLAN.md §6.1 Phase 9.5 — FFmpeg loudnorm, cible -16 LUFS (EBU R128).
    Principe P10 : loudness -16 LUFS.

    Args:
        input_path: Audio d'entree.
        output_path: Audio normalise en sortie.
        target_lufs: Cible de loudness en LUFS.

    Returns:
        Chemin vers le fichier normalise.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-i", str(input_path),
            "-af", f"loudnorm=I={target_lufs}:TP=-1.0:LRA=11",
            "-ar", "48000",
            "-ac", "1",
            str(output_path),
        ],
        description="loudnorm",
    )
    logger.info("Loudnorm applied: %s -> %s (target %.1f LUFS)", input_path, output_path, target_lufs)
    return output_path


def remux(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """Remux video source + audio final en MP4.

    Voir MASTERPLAN.md §6.1 Phase 11.1 — FFmpeg remux (video copy + audio AAC).
    Principe P14 : video source intouchee (copy, pas de reencodage).

    Args:
        video_path: Video source (piste video copiee sans reencodage).
        audio_path: Audio final (encode en AAC).
        output_path: MP4 de sortie.

    Returns:
        Chemin vers le MP4 final.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            str(output_path),
        ],
        description="remux",
    )
    logger.info("Remuxed: %s", output_path)
    return output_path
