#!/usr/bin/env python3
"""Comparative analysis of HeyGen vs SILA MOSS v2 vs SILA CosyVoice.

8 dimensions:
  1. Duration & timing accuracy
  2. Loudness (EBU R128)
  3. True peak
  4. Speech coverage & density
  5. Energy consistency (CoV)
  6. Spectral characteristics (centroid, bandwidth)
  7. DNSMOS speech quality
  8. File size / bitrate efficiency

Usage:
    python compare_engines.py /opt/sila/bench/comparison/
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

# ── Config ──────────────────────────────────────────────────────────────

ENGINES = {
    "heygen": "test_002_heygen_en.mp4",
    "moss_v2": "test_002_moss_v2_en.mp4",
    "cosyvoice": "test_002_cosyvoice_en.mp4",
}

RMS_WINDOW_MS = 200
RMS_SPEECH_THRESHOLD = 0.008
GAP_THRESHOLD_S = 2.0


# ── Helpers ─────────────────────────────────────────────────────────────


def extract_wav(mp4_path: str) -> tuple[str, float]:
    """Extract audio to temp WAV 48kHz mono."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    subprocess.run(
        ["ffmpeg", "-y", "-i", mp4_path, "-ar", "48000", "-ac", "1",
         "-acodec", "pcm_s16le", tmp.name],
        capture_output=True, check=True,
    )
    info = sf.info(tmp.name)
    return tmp.name, info.duration


def ffmpeg_loudnorm(path: str) -> dict:
    cmd = ["ffmpeg", "-i", path, "-af",
           "loudnorm=I=-16:TP=-1:LRA=11:print_format=json",
           "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    match = re.search(r'\{[^{}]*"input_i"[^{}]*\}', r.stderr, re.DOTALL)
    return json.loads(match.group()) if match else {}


def ffmpeg_volumedetect(path: str) -> dict:
    cmd = ["ffmpeg", "-i", path, "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    stats = {}
    for key in ("mean_volume", "max_volume"):
        m = re.search(rf"{key}:\s*([-\d.]+)\s*dB", r.stderr)
        if m:
            stats[key] = float(m.group(1))
    return stats


def ffprobe_info(path: str) -> dict:
    cmd = ["ffprobe", "-v", "quiet", "-show_format", "-show_streams",
           "-print_format", "json", path]
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def compute_speech_regions(samples: np.ndarray, sr: int) -> list[tuple[float, float]]:
    window = int(RMS_WINDOW_MS / 1000 * sr)
    hop = window // 2
    n_frames = (len(samples) - window) // hop

    is_speech = []
    for i in range(n_frames):
        frame = samples[i * hop: i * hop + window]
        rms = float(np.sqrt(np.mean(frame ** 2)))
        is_speech.append(rms >= RMS_SPEECH_THRESHOLD)

    regions = []
    in_region = False
    start = 0
    for i, s in enumerate(is_speech):
        t = i * hop / sr
        if s and not in_region:
            start = t
            in_region = True
        elif not s and in_region:
            regions.append((start, t))
            in_region = False
    if in_region:
        regions.append((start, len(samples) / sr))
    return regions


def spectral_analysis(samples: np.ndarray, sr: int) -> dict:
    """Compute spectral centroid and bandwidth."""
    import librosa
    centroid = librosa.feature.spectral_centroid(y=samples, sr=sr)[0]
    bandwidth = librosa.feature.spectral_bandwidth(y=samples, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(y=samples, sr=sr, roll_percent=0.90)[0]
    return {
        "centroid_mean": float(np.mean(centroid)),
        "centroid_std": float(np.std(centroid)),
        "bandwidth_mean": float(np.mean(bandwidth)),
        "rolloff_mean": float(np.mean(rolloff)),
    }


def compute_dnsmos(wav_path: str) -> float | None:
    """Compute DNSMOS if available in the pipeline venv."""
    try:
        # Try using the pipeline's DNSMOS engine
        sys.path.insert(0, "/opt/sila/app")
        from src.engines.qc.dnsmos_engine import compute_dnsmos_file
        return compute_dnsmos_file(wav_path)
    except Exception:
        pass

    # Fallback: try dnsmos CLI
    try:
        r = subprocess.run(
            ["/opt/sila/venv/bin/python", "-c",
             f"from src.engines.qc.dnsmos_engine import compute_dnsmos_file; "
             f"print(compute_dnsmos_file('{wav_path}'))"],
            capture_output=True, text=True, timeout=60,
            cwd="/opt/sila/app",
        )
        if r.returncode == 0:
            return float(r.stdout.strip())
    except Exception:
        pass
    return None


# ── Main analysis ───────────────────────────────────────────────────────


def analyze_one(name: str, mp4_path: str) -> dict:
    """Full 8-dimension analysis of a single MP4."""
    print(f"\n{'='*60}")
    print(f"  Analyzing: {name} ({Path(mp4_path).name})")
    print(f"{'='*60}")

    # File info
    file_size_mb = os.path.getsize(mp4_path) / 1e6
    probe = ffprobe_info(mp4_path)
    fmt = probe.get("format", {})
    duration = float(fmt.get("duration", 0))
    bitrate = int(fmt.get("bit_rate", 0)) / 1000  # kbps

    # Audio streams
    audio_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    audio_codec = audio_streams[0].get("codec_name", "?") if audio_streams else "?"
    audio_sr = int(audio_streams[0].get("sample_rate", 0)) if audio_streams else 0
    audio_bitrate = int(audio_streams[0].get("bit_rate", 0)) / 1000 if audio_streams else 0

    # Video streams
    video_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
    video_codec = video_streams[0].get("codec_name", "?") if video_streams else "?"
    video_res = ""
    if video_streams:
        w = video_streams[0].get("width", 0)
        h = video_streams[0].get("height", 0)
        video_res = f"{w}x{h}"

    print(f"  Duration: {duration:.1f}s | Size: {file_size_mb:.1f}MB | Bitrate: {bitrate:.0f}kbps")
    print(f"  Video: {video_codec} {video_res} | Audio: {audio_codec} {audio_sr}Hz {audio_bitrate:.0f}kbps")

    # Extract WAV
    wav_path, wav_dur = extract_wav(mp4_path)
    samples, sr = sf.read(wav_path, dtype="float32")
    if samples.ndim > 1:
        samples = samples.mean(axis=1)

    # 1. Duration
    print(f"  [1/8] Duration: {duration:.1f}s")

    # 2. Loudness
    loud = ffmpeg_loudnorm(mp4_path)
    lufs = float(loud.get("input_i", -99))
    lra = float(loud.get("input_lra", 0))
    print(f"  [2/8] Loudness: {lufs:.1f} LUFS (LRA: {lra:.1f})")

    # 3. True peak
    tp = float(loud.get("input_tp", 0))
    print(f"  [3/8] True peak: {tp:.1f} dBTP")

    # 4. Speech coverage
    regions = compute_speech_regions(samples, sr)
    speech_s = sum(e - s for s, e in regions)
    speech_pct = speech_s / duration * 100 if duration > 0 else 0
    n_regions = len(regions)
    print(f"  [4/8] Speech: {speech_pct:.1f}% ({speech_s:.1f}s, {n_regions} regions)")

    # 5. Energy consistency
    rms_values = []
    for s_t, e_t in regions:
        if e_t - s_t < 0.5:
            continue
        chunk = samples[int(s_t * sr):int(e_t * sr)]
        if len(chunk) > 0:
            rms_values.append(float(np.sqrt(np.mean(chunk ** 2))))
    mean_rms = np.mean(rms_values) if rms_values else 0
    std_rms = np.std(rms_values) if rms_values else 0
    cov = (std_rms / mean_rms * 100) if mean_rms > 0 else 0
    print(f"  [5/8] Energy CoV: {cov:.1f}% (mean RMS: {mean_rms:.4f})")

    # 6. Spectral analysis
    spec = spectral_analysis(samples, sr)
    print(f"  [6/8] Spectral: centroid={spec['centroid_mean']:.0f}Hz, "
          f"bandwidth={spec['bandwidth_mean']:.0f}Hz, rolloff={spec['rolloff_mean']:.0f}Hz")

    # 7. DNSMOS
    dnsmos = compute_dnsmos(wav_path)
    dnsmos_str = f"{dnsmos:.2f}" if dnsmos else "N/A"
    print(f"  [7/8] DNSMOS: {dnsmos_str}")

    # 8. Gaps
    vol = ffmpeg_volumedetect(mp4_path)
    mean_vol = vol.get("mean_volume", -99)
    max_vol = vol.get("max_volume", -99)
    big_gaps = sum(1 for i in range(1, len(regions))
                   if regions[i][0] - regions[i-1][1] > GAP_THRESHOLD_S)
    print(f"  [8/8] Gaps >2s: {big_gaps} | Vol: mean={mean_vol:.1f}dB max={max_vol:.1f}dB")

    # Cleanup
    Path(wav_path).unlink(missing_ok=True)

    return {
        "name": name,
        "file": Path(mp4_path).name,
        "file_size_mb": round(file_size_mb, 1),
        "duration_s": round(duration, 1),
        "bitrate_kbps": round(bitrate, 0),
        "video_codec": video_codec,
        "video_res": video_res,
        "audio_codec": audio_codec,
        "audio_sr": audio_sr,
        "audio_bitrate_kbps": round(audio_bitrate, 0),
        "loudness_lufs": round(lufs, 1),
        "lra": round(lra, 1),
        "true_peak_dbtp": round(tp, 1),
        "speech_coverage_pct": round(speech_pct, 1),
        "speech_regions": n_regions,
        "energy_cov_pct": round(cov, 1),
        "mean_rms": round(mean_rms, 5),
        "spectral_centroid_hz": round(spec["centroid_mean"], 0),
        "spectral_bandwidth_hz": round(spec["bandwidth_mean"], 0),
        "spectral_rolloff_hz": round(spec["rolloff_mean"], 0),
        "dnsmos": round(dnsmos, 2) if dnsmos else None,
        "mean_volume_db": round(mean_vol, 1),
        "gaps_gt_2s": big_gaps,
    }


def format_comparison_table(results: list[dict]) -> str:
    """Format side-by-side comparison table."""
    lines = []
    W = 80
    lines.append("")
    lines.append("=" * W)
    lines.append("  COMPARATIVE ANALYSIS — HeyGen vs SILA MOSS v2 vs SILA CosyVoice")
    lines.append("  Test: test_002 (52s conférence FR→EN)")
    lines.append("=" * W)

    # Header
    names = [r["name"] for r in results]
    header = f"{'Dimension':<28s}"
    for n in names:
        header += f"{'  ' + n:>17s}"
    lines.append(header)
    lines.append("-" * W)

    # Rows
    dims = [
        ("1. Duration", "duration_s", "s"),
        ("2. File size", "file_size_mb", "MB"),
        ("3. Bitrate", "bitrate_kbps", "kbps"),
        ("4. Loudness (LUFS)", "loudness_lufs", ""),
        ("5. LRA", "lra", ""),
        ("6. True peak (dBTP)", "true_peak_dbtp", ""),
        ("7. Speech coverage", "speech_coverage_pct", "%"),
        ("8. Speech regions", "speech_regions", ""),
        ("9. Energy CoV", "energy_cov_pct", "%"),
        ("10. Spectral centroid", "spectral_centroid_hz", "Hz"),
        ("11. Spectral bandwidth", "spectral_bandwidth_hz", "Hz"),
        ("12. Spectral rolloff 90%", "spectral_rolloff_hz", "Hz"),
        ("13. DNSMOS", "dnsmos", "/5"),
        ("14. Mean volume", "mean_volume_db", "dB"),
        ("15. Gaps >2s", "gaps_gt_2s", ""),
        ("16. Video codec", "video_codec", ""),
        ("17. Audio codec", "audio_codec", ""),
        ("18. Audio sample rate", "audio_sr", "Hz"),
    ]

    for label, key, unit in dims:
        row = f"{label:<28s}"
        for r in results:
            val = r.get(key, "N/A")
            if val is None:
                val_str = "N/A"
            elif isinstance(val, float):
                val_str = f"{val:.1f}{unit}"
            elif isinstance(val, int):
                val_str = f"{val}{unit}"
            else:
                val_str = str(val)
            row += f"{val_str:>17s}"
        lines.append(row)

    lines.append("-" * W)

    # Verdict
    lines.append("")
    lines.append("ANALYSIS NOTES:")

    # Find best DNSMOS
    dnsmos_vals = [(r["name"], r.get("dnsmos")) for r in results if r.get("dnsmos")]
    if dnsmos_vals:
        best = max(dnsmos_vals, key=lambda x: x[1])
        lines.append(f"  - Best DNSMOS: {best[0]} ({best[1]:.2f}/5)")

    # Loudness closest to -16
    lufs_vals = [(r["name"], abs(r["loudness_lufs"] - (-16))) for r in results]
    best_lufs = min(lufs_vals, key=lambda x: x[1])
    lines.append(f"  - Closest to -16 LUFS: {best_lufs[0]} (delta={best_lufs[1]:.1f})")

    # Best speech coverage
    cov_vals = [(r["name"], r["speech_coverage_pct"]) for r in results]
    best_cov = max(cov_vals, key=lambda x: x[1])
    lines.append(f"  - Best speech coverage: {best_cov[0]} ({best_cov[1]:.1f}%)")

    # Lowest energy CoV (most consistent)
    ecov_vals = [(r["name"], r["energy_cov_pct"]) for r in results]
    best_ecov = min(ecov_vals, key=lambda x: x[1])
    lines.append(f"  - Most consistent energy: {best_ecov[0]} (CoV={best_ecov[1]:.1f}%)")

    # Smallest file
    size_vals = [(r["name"], r["file_size_mb"]) for r in results]
    best_size = min(size_vals, key=lambda x: x[1])
    lines.append(f"  - Smallest file: {best_size[0]} ({best_size[1]:.1f}MB)")

    lines.append("")
    lines.append("=" * W)

    return "\n".join(lines)


def main():
    comp_dir = sys.argv[1] if len(sys.argv) > 1 else "/opt/sila/bench/comparison"
    comp_dir = Path(comp_dir)

    if not comp_dir.exists():
        print(f"Error: {comp_dir} not found")
        sys.exit(1)

    results = []
    for name, filename in ENGINES.items():
        mp4_path = comp_dir / filename
        if not mp4_path.exists():
            print(f"WARN: {mp4_path} not found, skipping {name}")
            continue
        r = analyze_one(name, str(mp4_path))
        results.append(r)

    if not results:
        print("No files found for comparison!")
        sys.exit(1)

    # Print comparison table
    table = format_comparison_table(results)
    print(table)

    # Save JSON
    out_json = comp_dir / "comparison_report.json"
    with open(out_json, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nJSON report: {out_json}")

    # Save text report
    out_txt = comp_dir / "comparison_report.txt"
    with open(out_txt, "w") as f:
        f.write(table)
    print(f"Text report: {out_txt}")


if __name__ == "__main__":
    main()
