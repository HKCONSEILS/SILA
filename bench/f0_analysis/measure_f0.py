#!/usr/bin/env python3
"""Mesure F0 robuste avec correction octave doubling.
Uses pyin with dual-range detection + autocorrelation cross-check."""
import numpy as np
import librosa
import json
import sys
import argparse
from pathlib import Path


def estimate_f0_autocorrelation(y, sr, frame_length=4096, hop_length=2048):
    """F0 via autocorrelation — robust to octave doubling."""
    min_period = int(sr / 300)
    max_period = int(sr / 60)
    f0_estimates = []
    for start in range(0, len(y) - frame_length, hop_length):
        frame = y[start:start + frame_length]
        rms = np.sqrt(np.mean(frame ** 2))
        if rms < 0.01:
            continue
        corr = np.correlate(frame, frame, mode='full')
        corr = corr[len(corr)//2:]
        if corr[0] > 0:
            corr = corr / corr[0]
        search = corr[min_period:max_period]
        if len(search) == 0:
            continue
        peak_idx = np.argmax(search) + min_period
        if corr[peak_idx] > 0.3:
            f0_estimates.append(sr / peak_idx)
    if f0_estimates:
        return float(np.median(f0_estimates))
    return None


def measure_f0_pyin_corrected(wav_path, sr=48000):
    """pyin + octave doubling correction + autocorrelation cross-check."""
    y, sr = librosa.load(str(wav_path), sr=sr, mono=True)

    # Method 1: pyin wide range [50-400 Hz]
    f0_wide, _, _ = librosa.pyin(y, fmin=50, fmax=400, sr=sr,
                                  frame_length=2048, hop_length=512)
    f0_wide_valid = f0_wide[~np.isnan(f0_wide)]
    if len(f0_wide_valid) == 0:
        return {"error": "no F0 detected", "method": "pyin_wide"}

    median_wide = float(np.median(f0_wide_valid))

    # Method 2: pyin male range [50-200 Hz]
    f0_low, _, _ = librosa.pyin(y, fmin=50, fmax=200, sr=sr,
                                 frame_length=2048, hop_length=512)
    f0_low_valid = f0_low[~np.isnan(f0_low)]
    median_low = float(np.median(f0_low_valid)) if len(f0_low_valid) > 0 else None

    # Method 3: pyin female range [150-350 Hz]
    f0_high, _, _ = librosa.pyin(y, fmin=150, fmax=350, sr=sr,
                                  frame_length=2048, hop_length=512)
    f0_high_valid = f0_high[~np.isnan(f0_high)]
    median_high = float(np.median(f0_high_valid)) if len(f0_high_valid) > 0 else None

    # Method 4: autocorrelation
    f0_autocorr = estimate_f0_autocorrelation(y, sr)

    # Octave doubling detection
    octave_corrected = False
    f0_final = median_wide
    method = "pyin_wide"

    if median_low and median_low > 0:
        ratio = median_wide / median_low
        if 1.8 < ratio < 2.2:
            f0_final = median_low
            octave_corrected = True
            method = "pyin_low (octave corrected)"
        elif ratio < 1.3:
            method = "pyin_wide (confirmed by low range)"

    # Cross-check with autocorrelation
    if f0_autocorr and f0_autocorr > 0:
        autocorr_ratio = f0_final / f0_autocorr
        if 1.8 < autocorr_ratio < 2.2:
            f0_final = f0_autocorr
            octave_corrected = True
            method = "autocorrelation (octave corrected)"

    # Stats on the correct range
    if octave_corrected and median_low and len(f0_low_valid) > 0:
        f0_for_stats = f0_low_valid
    else:
        f0_for_stats = f0_wide_valid

    med = np.median(f0_for_stats)
    std = np.std(f0_for_stats)
    f0_filtered = f0_for_stats[(f0_for_stats > med - 2*std) & (f0_for_stats < med + 2*std)]
    if len(f0_filtered) < 10:
        f0_filtered = f0_for_stats

    # Voice classification
    if f0_final < 150: voice_type = "masculine grave"
    elif f0_final < 185: voice_type = "masculine medium"
    elif f0_final < 220: voice_type = "féminine grave"
    elif f0_final < 280: voice_type = "féminine medium"
    else: voice_type = "féminine aiguë"

    return {
        "f0_mean": round(float(np.mean(f0_filtered)), 1),
        "f0_median": round(float(np.median(f0_filtered)), 1),
        "f0_std": round(float(np.std(f0_filtered)), 1),
        "f0_cov": round(float(np.std(f0_filtered) / np.mean(f0_filtered) * 100), 1),
        "voiced_pct": round(float(np.sum(~np.isnan(f0_wide)) / len(f0_wide) * 100), 1),
        "n_frames": len(f0_filtered),
        "voice_type": voice_type,
        "method": method,
        "octave_corrected": octave_corrected,
        "raw_measurements": {
            "pyin_wide_median": round(float(median_wide), 1),
            "pyin_low_median": round(float(median_low), 1) if median_low else None,
            "pyin_high_median": round(float(median_high), 1) if median_high else None,
            "autocorrelation": round(float(f0_autocorr), 1) if f0_autocorr else None,
        }
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mesure F0 robuste")
    parser.add_argument("wav_files", nargs="+", help="WAV files")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    results = {}
    for wav in args.wav_files:
        p = Path(wav)
        if not p.exists():
            print(f"SKIP: {wav}")
            continue
        print(f"\n{'─'*50}")
        print(f"Analyse: {p.parent.name}/{p.name}" if "stems" in str(p) else f"Analyse: {p.name}")
        r = measure_f0_pyin_corrected(wav)
        # Use parent dir name as key for stems
        key = p.parent.name if p.name == "vocals.wav" else p.stem
        results[key] = r

        if "error" in r:
            print(f"  ERREUR: {r['error']}")
            continue

        print(f"  F0 median:  {r['f0_median']} Hz")
        print(f"  F0 mean:    {r['f0_mean']} Hz")
        print(f"  F0 CoV:     {r['f0_cov']}%")
        print(f"  Voice type: {r['voice_type']}")
        print(f"  Method:     {r['method']}")
        print(f"  Octave fix: {r['octave_corrected']}")
        raw = r['raw_measurements']
        print(f"  Raw: wide={raw['pyin_wide_median']}, low={raw['pyin_low_median']}, "
              f"autocorr={raw['autocorrelation']}")

    out = Path("/opt/sila/bench/f0_analysis/f0_corrected_results.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nRapport: {out}")
