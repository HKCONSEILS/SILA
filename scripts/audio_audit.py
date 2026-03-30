#!/usr/bin/env python3
"""SILA Audio Audit — automated quality assessment for dubbed MP4/WAV exports.

Usage:
    python scripts/audio_audit.py exports/output_en.mp4
    python scripts/audio_audit.py exports/output_en.mp4 --manifest manifest.json
    python scripts/audio_audit.py exports/output_en.mp4 --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np


# ── Thresholds ────────────────────────────────────────────────────────────

THRESHOLDS = {
    "audio_presence": {"pass": -30, "unit": "dB"},
    "loudness": {"pass": (-17, -15), "warn": (-19, -13), "unit": "LUFS"},
    "true_peak": {"pass": -1.0, "warn": 0.0, "unit": "dBTP"},
    "speech_coverage": {"pass": 60, "warn": 40, "unit": "%"},
    "fragmentation": {"pass": 15, "warn": 25, "unit": "reg/min"},
    "energy_consistency": {"pass": 25, "warn": 40, "unit": "% CoV"},
    "gaps": {"pass": 0, "warn": 2, "unit": "gaps >2s"},
    "tail_silence": {"pass": 0.005, "warn": 0.001, "unit": "RMS"},
}

RMS_WINDOW_MS = 200
RMS_SPEECH_THRESHOLD = 0.008
GAP_THRESHOLD_S = 2.0


def _extract_wav(input_path: str) -> tuple[str, float]:
    """Extract audio to temp WAV 48kHz mono."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-ar", "48000", "-ac", "1",
         "-acodec", "pcm_s16le", tmp.name],
        capture_output=True, check=True,
    )
    import soundfile as sf
    info = sf.info(tmp.name)
    return tmp.name, info.duration


def _ffmpeg_loudnorm(input_path: str) -> dict:
    cmd = [
        "ffmpeg", "-i", input_path,
        "-af", "loudnorm=I=-16:TP=-1:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    match = re.search(r'\{[^{}]*"input_i"[^{}]*\}', result.stderr, re.DOTALL)
    return json.loads(match.group()) if match else {}


def _ffmpeg_volumedetect(input_path: str) -> dict:
    cmd = ["ffmpeg", "-i", input_path, "-af", "volumedetect", "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    stats = {}
    for key in ("mean_volume", "max_volume"):
        m = re.search(rf"{key}:\s*([-\d.]+)\s*dB", result.stderr)
        if m:
            stats[key] = float(m.group(1))
    return stats


def _compute_speech_regions(samples: np.ndarray, sr: int) -> list[tuple[float, float]]:
    window = int(RMS_WINDOW_MS / 1000 * sr)
    hop = window // 2
    n_frames = (len(samples) - window) // hop

    is_speech = []
    for i in range(n_frames):
        frame = samples[i * hop : i * hop + window]
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


def check_audio_presence(vol_stats: dict) -> dict:
    mean_vol = vol_stats.get("mean_volume", -99)
    status = "PASS" if mean_vol > THRESHOLDS["audio_presence"]["pass"] else "FAIL"
    return {"status": status, "value": round(mean_vol, 1), "unit": "dB"}


def check_loudness(loud_stats: dict) -> dict:
    lufs = float(loud_stats.get("input_i", -99))
    p = THRESHOLDS["loudness"]["pass"]
    w = THRESHOLDS["loudness"]["warn"]
    if p[0] <= lufs <= p[1]:
        status = "PASS"
    elif w[0] <= lufs <= w[1]:
        status = "WARNING"
    else:
        status = "FAIL"
    return {"status": status, "value": round(lufs, 1), "unit": "LUFS"}


def check_true_peak(loud_stats: dict) -> dict:
    tp = float(loud_stats.get("input_tp", 0))
    if tp < THRESHOLDS["true_peak"]["pass"]:
        status = "PASS"
    elif tp < THRESHOLDS["true_peak"]["warn"]:
        status = "WARNING"
    else:
        status = "FAIL"
    return {"status": status, "value": round(tp, 1), "unit": "dBTP"}


def check_speech_coverage(regions: list, duration_s: float) -> dict:
    speech_s = sum(e - s for s, e in regions)
    pct = speech_s / duration_s * 100 if duration_s > 0 else 0
    if pct >= THRESHOLDS["speech_coverage"]["pass"]:
        status = "PASS"
    elif pct >= THRESHOLDS["speech_coverage"]["warn"]:
        status = "WARNING"
    else:
        status = "FAIL"
    return {"status": status, "value": round(pct, 1), "unit": "%"}


def check_fragmentation(regions: list, duration_s: float) -> dict:
    dur_min = duration_s / 60 if duration_s > 0 else 1
    reg_per_min = len(regions) / dur_min
    if reg_per_min <= THRESHOLDS["fragmentation"]["pass"]:
        status = "PASS"
    elif reg_per_min <= THRESHOLDS["fragmentation"]["warn"]:
        status = "WARNING"
    else:
        status = "FAIL"
    return {"status": status, "value": round(reg_per_min, 1), "unit": "reg/min"}


def check_energy_consistency(samples: np.ndarray, sr: int, regions: list) -> dict:
    rms_values = []
    for s, e in regions:
        if e - s < 0.5:
            continue
        start = int(s * sr)
        end = int(e * sr)
        chunk = samples[start:end]
        if len(chunk) > 0:
            rms_values.append(float(np.sqrt(np.mean(chunk ** 2))))

    if len(rms_values) < 2:
        return {"status": "PASS", "value": 0.0, "unit": "% CoV"}

    mean_rms = np.mean(rms_values)
    std_rms = np.std(rms_values)
    cov = (std_rms / mean_rms * 100) if mean_rms > 0 else 0

    if cov <= THRESHOLDS["energy_consistency"]["pass"]:
        status = "PASS"
    elif cov <= THRESHOLDS["energy_consistency"]["warn"]:
        status = "WARNING"
    else:
        status = "FAIL"
    return {"status": status, "value": round(cov, 1), "unit": "% CoV"}


def check_gaps(regions: list) -> dict:
    big_gaps = 0
    for i in range(1, len(regions)):
        gap = regions[i][0] - regions[i - 1][1]
        if gap > GAP_THRESHOLD_S:
            big_gaps += 1

    if big_gaps <= THRESHOLDS["gaps"]["pass"]:
        status = "PASS"
    elif big_gaps <= THRESHOLDS["gaps"]["warn"]:
        status = "WARNING"
    else:
        status = "FAIL"
    return {"status": status, "value": big_gaps, "unit": "gaps >2s"}


def check_tail_silence(samples: np.ndarray, sr: int, duration_s: float) -> dict:
    tail_start = int(len(samples) * 0.9)
    tail = samples[tail_start:]
    rms = float(np.sqrt(np.mean(tail ** 2))) if len(tail) > 0 else 0

    if rms > THRESHOLDS["tail_silence"]["pass"]:
        status = "PASS"
    elif rms > THRESHOLDS["tail_silence"]["warn"]:
        status = "WARNING"
    else:
        status = "FAIL"

    tail_dur = duration_s * 0.1
    return {"status": status, "value": round(rms, 5), "unit": f"RMS (last {tail_dur:.1f}s)"}


def check_qc_timing(manifest_path: str) -> dict | None:
    qc_path = Path(manifest_path).parent / "qc_report.json"
    if not qc_path.exists():
        return None
    try:
        with open(qc_path) as f:
            qc = json.load(f)
        rate = qc.get("pass_rate", 0) * 100
        if rate >= 70:
            status = "PASS"
        elif rate >= 50:
            status = "WARNING"
        else:
            status = "FAIL"
        return {"status": status, "value": round(rate, 1), "unit": "%"}
    except Exception:
        return None


def check_dnsmos_from_manifest(manifest_path: str) -> dict | None:
    qc_path = Path(manifest_path).parent / "qc_report.json"
    if not qc_path.exists():
        return None
    try:
        with open(qc_path) as f:
            qc = json.load(f)
        dnsmos = qc.get("dnsmos", {})
        mean_mos = dnsmos.get("mean", 0)
        if mean_mos <= 0:
            return None
        if mean_mos >= 3.0:
            status = "PASS"
        elif mean_mos >= 2.5:
            status = "WARNING"
        else:
            status = "FAIL"
        return {"status": status, "value": round(mean_mos, 2), "unit": "/5.0 MOS"}
    except Exception:
        return None


def run_audit(input_path: str, manifest_path: str | None = None) -> dict:
    """Run full audio audit. Returns structured report."""
    import soundfile as sf

    input_name = Path(input_path).name
    wav_path, duration_s = _extract_wav(input_path)

    samples, sr = sf.read(wav_path, dtype="float32")
    if samples.ndim > 1:
        samples = samples.mean(axis=1)

    vol_stats = _ffmpeg_volumedetect(input_path)
    loud_stats = _ffmpeg_loudnorm(input_path)
    regions = _compute_speech_regions(samples, sr)

    checks = {}
    checks["audio_presence"] = check_audio_presence(vol_stats)
    checks["loudness"] = check_loudness(loud_stats)
    checks["true_peak"] = check_true_peak(loud_stats)
    checks["speech_coverage"] = check_speech_coverage(regions, duration_s)
    checks["fragmentation"] = check_fragmentation(regions, duration_s)
    checks["energy_consistency"] = check_energy_consistency(samples, sr, regions)
    checks["gaps"] = check_gaps(regions)
    checks["tail_silence"] = check_tail_silence(samples, sr, duration_s)

    if manifest_path:
        qc = check_qc_timing(manifest_path)
        if qc:
            checks["qc_timing"] = qc
        dnsmos = check_dnsmos_from_manifest(manifest_path)
        if dnsmos:
            checks["dnsmos"] = dnsmos

    statuses = [c["status"] for c in checks.values()]
    n_pass = statuses.count("PASS")
    n_warn = statuses.count("WARNING")
    n_fail = statuses.count("FAIL")

    if n_fail > 0:
        verdict = "FAIL"
    elif n_warn > 0:
        verdict = "WARNING"
    else:
        verdict = "PASS"

    Path(wav_path).unlink(missing_ok=True)

    return {
        "file": input_name,
        "duration_s": round(duration_s, 1),
        "checks": checks,
        "summary": {"pass": n_pass, "warning": n_warn, "fail": n_fail, "verdict": verdict},
    }


def _format_console(report: dict) -> str:
    W = 52
    lines = []
    lines.append("+" + "=" * W + "+")
    title = f"SILA AUDIO AUDIT - {report['file']}"
    lines.append("|" + title.center(W) + "|")
    lines.append("|" + f"Duration: {report['duration_s']}s".center(W) + "|")
    lines.append("+" + "-" * W + "+")

    labels = {
        "audio_presence": "Audio presence",
        "loudness": "Loudness",
        "true_peak": "True peak",
        "speech_coverage": "Speech coverage",
        "fragmentation": "Fragmentation",
        "energy_consistency": "Energy consistency",
        "gaps": "Gaps",
        "tail_silence": "Tail silence",
        "qc_timing": "QC timing",
        "dnsmos": "DNSMOS",
    }
    icons = {"PASS": "PASS", "WARNING": "WARN", "FAIL": "FAIL"}

    for i, (key, check) in enumerate(report["checks"].items(), 1):
        label = labels.get(key, key)
        icon = icons[check["status"]]
        val = f"{check['value']} {check['unit']}"
        marker = "  " if check["status"] == "PASS" else ">>" if check["status"] == "FAIL" else " >"
        line = f"{marker} {i:2d}. {label:<22s} {icon:<6s} {val}"
        lines.append("| " + line.ljust(W - 1) + "|")

    lines.append("+" + "-" * W + "+")
    s = report["summary"]
    verdict_line = f"VERDICT: {s['verdict']}  ({s['pass']} pass, {s['warning']} warn, {s['fail']} fail)"
    lines.append("|" + verdict_line.center(W) + "|")
    lines.append("+" + "=" * W + "+")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="SILA Audio Audit")
    parser.add_argument("input", help="Path to MP4 or WAV file")
    parser.add_argument("--manifest", default=None, help="Path to manifest.json")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: {args.input} not found", file=sys.stderr)
        sys.exit(1)

    report = run_audit(args.input, args.manifest)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_format_console(report))

    sys.exit(1 if report["summary"]["verdict"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
