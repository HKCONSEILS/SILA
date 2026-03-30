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
    """Normalise le loudness en 2 passes FFmpeg (EBU R128).

    Passe 1 : mesurer le loudness actuel.
    Passe 2 : appliquer la correction avec linear=true.
    Voir MASTERPLAN.md §6.1 Phase 9.5 — P10 : -16 LUFS.
    """
    import re as _re

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Pass 1: measure
    measure_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
        "-i", str(input_path),
        "-af", f"loudnorm=I={target_lufs}:TP=-1.0:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(measure_cmd, capture_output=True, text=True, check=False)
    match = _re.search(r'\{[^{}]*"input_i"[^{}]*\}', result.stderr, _re.DOTALL)

    if match:
        stats = json.loads(match.group())
        # Check for -inf values (happens with mostly-silent audio)
        has_inf = any("inf" in str(v).lower() for v in stats.values())
        if has_inf:
            logger.warning("Loudnorm: measured values contain -inf (mostly silent audio), copying as-is")
            import shutil
            shutil.copy2(str(input_path), str(output_path))
            return output_path
        # Pass 2: correct with measured values
        af = (
            f"loudnorm=I={target_lufs}:TP=-1.0:LRA=11:"
            f"measured_I={stats.get('input_i', -24)}:"
            f"measured_LRA={stats.get('input_lra', 7)}:"
            f"measured_TP={stats.get('input_tp', -2)}:"
            f"measured_thresh={stats.get('input_thresh', -34)}:"
            f"offset={stats.get('target_offset', 0)}:"
            f"linear=true"
        )
        _run_ffmpeg(
            ["-i", str(input_path), "-af", af, "-ar", "48000", "-ac", "1", str(output_path)],
            description="loudnorm-2pass",
        )
        logger.info("Loudnorm 2-pass: %s (measured_I=%s)", output_path, stats.get("input_i"))
    else:
        # Fallback: single pass
        logger.warning("Loudnorm: could not parse stats, falling back to 1-pass")
        _run_ffmpeg(
            ["-i", str(input_path), "-af", f"loudnorm=I={target_lufs}:TP=-1.0:LRA=11",
             "-ar", "48000", "-ac", "1", str(output_path)],
            description="loudnorm-1pass",
        )
    return output_path


def remux(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    target_lang: str = "en",
) -> Path:
    """Remux video source + audio final en MP4.

    Voir MASTERPLAN.md §6.1 Phase 11.1 — FFmpeg remux (video copy + audio AAC).
    P14 : video copy (jamais reencodee). P5 : audio 48kHz.
    +faststart pour streaming. Metadata langue sur la piste audio.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-map", "0:v:0",
            "-map", "1:a:0",
            f"-metadata:s:a:0", f"language={target_lang}",
            "-movflags", "+faststart",
            "-shortest",
            str(output_path),
        ],
        description="remux",
    )
    logger.info("Remuxed: %s (lang=%s, faststart)", output_path, target_lang)
    return output_path


def remux_multitrack(
    video_path: Path,
    mix_audio: Path,
    voice_audio: Path,
    background_audio: Path | None,
    output_path: Path,
    target_lang: str = "en",
) -> Path:
    """Remux video + 3 audio tracks (mix, voice, background) into MP4.

    Creates a multi-track MP4 for professional post-production.
    Track 0: video (copy), Track 1: mix, Track 2: voice, Track 3: background.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "-i", str(video_path),
        "-i", str(mix_audio),
        "-i", str(voice_audio),
    ]
    if background_audio and background_audio.exists():
        cmd.extend(["-i", str(background_audio)])
        n_audio = 3
    else:
        n_audio = 2

    cmd.extend([
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-map", "0:v:0",
        "-map", "1:a:0", "-metadata:s:a:0", f"language={target_lang}", "-metadata:s:a:0", "title=Mix",
        "-map", "2:a:0", "-metadata:s:a:1", f"language={target_lang}", "-metadata:s:a:1", "title=Voice",
    ])
    if n_audio == 3:
        cmd.extend([
            "-map", "3:a:0", "-metadata:s:a:2", f"language={target_lang}", "-metadata:s:a:2", "title=Background",
        ])
    cmd.extend(["-movflags", "+faststart", "-shortest", str(output_path)])

    _run_ffmpeg(cmd, description="remux_multitrack")
    logger.info("Multitrack remuxed: %s (%d audio tracks, lang=%s)", output_path, n_audio, target_lang)
    return output_path
