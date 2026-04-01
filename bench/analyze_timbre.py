#!/usr/bin/env python3
"""Timbre analysis — Speaker embedding, MFCC, Pitch (F0), DNSMOS.

Compares HeyGen vs SILA MOSS v2 vs SILA CosyVoice on timbre uniformity
and speech quality.

Usage:
    python analyze_timbre.py /opt/sila/bench/comparison/
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

# ── Config ──────────────────────────────────────────────────────────────

ENGINES = {
    "HeyGen": "test_002_heygen_en.mp4",
    "MOSS v2": "test_002_moss_v2_en.mp4",
    "CosyVoice": "test_002_cosyvoice_en.mp4",
}

RMS_WINDOW_MS = 200
RMS_SPEECH_THRESHOLD = 0.008
MIN_SEGMENT_S = 0.5


# ── Helpers ─────────────────────────────────────────────────────────────


def extract_wav_16k(mp4_path: str) -> str:
    """Extract audio to temp WAV 16kHz mono (for resemblyzer)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    subprocess.run(
        ["ffmpeg", "-y", "-i", mp4_path, "-ar", "16000", "-ac", "1",
         "-acodec", "pcm_s16le", tmp.name],
        capture_output=True, check=True,
    )
    return tmp.name


def extract_wav_48k(mp4_path: str) -> str:
    """Extract audio to temp WAV 48kHz mono (for DNSMOS)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    subprocess.run(
        ["ffmpeg", "-y", "-i", mp4_path, "-ar", "48000", "-ac", "1",
         "-acodec", "pcm_s16le", tmp.name],
        capture_output=True, check=True,
    )
    return tmp.name


def detect_speech_regions(samples: np.ndarray, sr: int) -> list[tuple[float, float]]:
    """VAD by RMS energy."""
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
            if t - start >= MIN_SEGMENT_S:
                regions.append((start, t))
            in_region = False
    if in_region:
        end_t = len(samples) / sr
        if end_t - start >= MIN_SEGMENT_S:
            regions.append((start, end_t))

    return regions


# ── Speaker Embedding Analysis ──────────────────────────────────────────


def analyze_speaker_embedding(samples: np.ndarray, sr: int,
                               regions: list[tuple[float, float]]) -> dict:
    """Compute speaker embeddings per segment and cosine similarity matrix."""
    from resemblyzer import VoiceEncoder, preprocess_wav

    encoder = VoiceEncoder()

    embeddings = []
    for s, e in regions:
        chunk = samples[int(s * sr):int(e * sr)]
        if len(chunk) < sr * 0.3:  # skip very short
            continue
        # Resemblyzer expects float32 [-1, 1]
        chunk = chunk.astype(np.float32)
        processed = preprocess_wav(chunk, source_sr=sr)
        if len(processed) < 1600:  # min length for encoder
            continue
        emb = encoder.embed_utterance(processed)
        embeddings.append(emb)

    if len(embeddings) < 2:
        return {
            "similarity_mean": 0.0,
            "similarity_min": 0.0,
            "similarity_std": 0.0,
            "outliers": 0,
            "n_segments": len(embeddings),
        }

    # Cosine similarity matrix
    emb_matrix = np.array(embeddings)
    n = len(emb_matrix)
    similarities = []
    for i in range(n):
        for j in range(i + 1, n):
            cos_sim = float(np.dot(emb_matrix[i], emb_matrix[j]) /
                          (np.linalg.norm(emb_matrix[i]) * np.linalg.norm(emb_matrix[j])))
            similarities.append(cos_sim)

    # Mean embedding for outlier detection
    mean_emb = emb_matrix.mean(axis=0)
    mean_emb /= np.linalg.norm(mean_emb)
    per_seg_sim = []
    for emb in emb_matrix:
        cos_sim = float(np.dot(emb, mean_emb) / np.linalg.norm(emb))
        per_seg_sim.append(cos_sim)

    outliers = sum(1 for s in per_seg_sim if s < 0.80)

    return {
        "similarity_mean": round(float(np.mean(similarities)), 4),
        "similarity_min": round(float(np.min(similarities)), 4),
        "similarity_std": round(float(np.std(similarities)), 4),
        "outliers": outliers,
        "n_segments": n,
    }


# ── MFCC Analysis ──────────────────────────────────────────────────────


def analyze_mfcc(samples: np.ndarray, sr: int,
                  regions: list[tuple[float, float]]) -> dict:
    """MFCC inter-segment variance (lower = more uniform timbre)."""
    segment_mfccs = []
    for s, e in regions:
        chunk = samples[int(s * sr):int(e * sr)]
        if len(chunk) < sr * 0.3:
            continue
        mfcc = librosa.feature.mfcc(y=chunk.astype(np.float32), sr=sr, n_mfcc=13)
        mean_mfcc = mfcc.mean(axis=1)  # 13-dim vector
        segment_mfccs.append(mean_mfcc)

    if len(segment_mfccs) < 2:
        return {"variance_inter_seg": 0.0, "n_segments": len(segment_mfccs)}

    mfcc_matrix = np.array(segment_mfccs)
    # Variance of each MFCC across segments, then sum
    variance = float(np.mean(np.var(mfcc_matrix, axis=0)))

    return {
        "variance_inter_seg": round(variance, 2),
        "n_segments": len(segment_mfccs),
    }


# ── Pitch (F0) Analysis ────────────────────────────────────────────────


def analyze_pitch(samples: np.ndarray, sr: int,
                   regions: list[tuple[float, float]]) -> dict:
    """F0 analysis per segment using librosa.pyin."""
    segment_f0s = []
    for s, e in regions:
        chunk = samples[int(s * sr):int(e * sr)]
        if len(chunk) < sr * 0.3:
            continue
        f0, voiced_flag, voiced_prob = librosa.pyin(
            chunk.astype(np.float32), fmin=50, fmax=500, sr=sr)
        # Filter only voiced frames
        voiced_f0 = f0[~np.isnan(f0)]
        if len(voiced_f0) > 0:
            segment_f0s.append(float(np.mean(voiced_f0)))

    if len(segment_f0s) < 2:
        return {"f0_mean": 0.0, "f0_cov_pct": 0.0, "n_segments": len(segment_f0s)}

    f0_mean = float(np.mean(segment_f0s))
    f0_std = float(np.std(segment_f0s))
    f0_cov = (f0_std / f0_mean * 100) if f0_mean > 0 else 0

    return {
        "f0_mean": round(f0_mean, 1),
        "f0_cov_pct": round(f0_cov, 1),
        "n_segments": len(segment_f0s),
    }


# ── DNSMOS ──────────────────────────────────────────────────────────────


def compute_dnsmos(wav_path_48k: str) -> dict:
    """Compute DNSMOS on the full audio."""
    try:
        audio, sr = sf.read(wav_path_48k, dtype="float32")
        if audio.ndim > 1:
            audio = audio[:, 0]
        # Resample to 16kHz
        audio_16k = librosa.resample(audio, orig_sr=sr, target_sr=16000)
        audio_16k = np.clip(audio_16k, -1.0, 1.0)

        from speechmos import dnsmos
        result = dnsmos.run(audio_16k, 16000, return_df=True, verbose=False)
        return {
            "ovrl_mos": round(float(result.get("ovrl_mos", 0)), 3),
            "sig_mos": round(float(result.get("sig_mos", 0)), 3),
            "bak_mos": round(float(result.get("bak_mos", 0)), 3),
            "p808_mos": round(float(result.get("p808_mos", 0)), 3),
        }
    except Exception as exc:
        print(f"  DNSMOS failed: {exc}")
        return {"ovrl_mos": 0, "sig_mos": 0, "bak_mos": 0, "p808_mos": 0}


# ── Main ────────────────────────────────────────────────────────────────


def analyze_one(name: str, mp4_path: str) -> dict:
    """Full timbre + DNSMOS analysis for one MP4."""
    print(f"\n{'='*60}")
    print(f"  Timbre analysis: {name}")
    print(f"{'='*60}")

    # Extract audio at 16kHz (resemblyzer, MFCC, pitch)
    wav_16k = extract_wav_16k(mp4_path)
    samples_16k, sr_16k = sf.read(wav_16k, dtype="float32")
    if samples_16k.ndim > 1:
        samples_16k = samples_16k.mean(axis=1)

    # Extract at 48kHz (DNSMOS)
    wav_48k = extract_wav_48k(mp4_path)

    # Detect speech regions
    regions = detect_speech_regions(samples_16k, sr_16k)
    print(f"  Speech regions: {len(regions)} (min dur: {MIN_SEGMENT_S}s)")

    # 1. Speaker embedding
    print("  [1/4] Speaker embedding...")
    spk = analyze_speaker_embedding(samples_16k, sr_16k, regions)
    print(f"         sim_mean={spk['similarity_mean']:.4f} "
          f"sim_min={spk['similarity_min']:.4f} "
          f"outliers={spk['outliers']}/{spk['n_segments']}")

    # 2. MFCC
    print("  [2/4] MFCC analysis...")
    mfcc = analyze_mfcc(samples_16k, sr_16k, regions)
    print(f"         variance_inter_seg={mfcc['variance_inter_seg']:.2f}")

    # 3. Pitch
    print("  [3/4] Pitch (F0)...")
    pitch = analyze_pitch(samples_16k, sr_16k, regions)
    print(f"         f0_mean={pitch['f0_mean']:.1f}Hz "
          f"f0_cov={pitch['f0_cov_pct']:.1f}%")

    # 4. DNSMOS
    print("  [4/4] DNSMOS...")
    dnsmos = compute_dnsmos(wav_48k)
    print(f"         ovrl={dnsmos['ovrl_mos']:.3f} "
          f"sig={dnsmos['sig_mos']:.3f} "
          f"bak={dnsmos['bak_mos']:.3f}")

    # Cleanup
    Path(wav_16k).unlink(missing_ok=True)
    Path(wav_48k).unlink(missing_ok=True)

    return {
        "name": name,
        "speaker_embedding": spk,
        "mfcc": mfcc,
        "pitch": pitch,
        "dnsmos": dnsmos,
    }


def format_report(results: list[dict]) -> str:
    """Format the timbre analysis report."""
    lines = []
    W = 72
    lines.append("")
    lines.append("=" * W)
    lines.append("  ANALYSE TIMBRE — test_002 (52s conférence FR→EN)")
    lines.append("=" * W)

    names = [r["name"] for r in results]
    header = f"{'':28s}"
    for n in names:
        header += f"{n:>15s}"
    lines.append(header)
    lines.append("-" * W)

    # Speaker embedding
    lines.append("--- Speaker Embedding ---")
    for label, key in [("Similarity moyenne", "similarity_mean"),
                        ("Similarity min", "similarity_min"),
                        ("Similarity std", "similarity_std"),
                        ("Outliers (<0.80)", "outliers")]:
        row = f"  {label:<26s}"
        for r in results:
            v = r["speaker_embedding"][key]
            if isinstance(v, float):
                row += f"{v:>15.4f}"
            else:
                row += f"{v:>15d}"
        lines.append(row)

    # MFCC
    lines.append("")
    lines.append("--- MFCC ---")
    row = f"  {'Variance inter-seg':<26s}"
    for r in results:
        row += f"{r['mfcc']['variance_inter_seg']:>15.2f}"
    lines.append(row)

    # Pitch
    lines.append("")
    lines.append("--- Pitch (F0) ---")
    for label, key, fmt in [("F0 moyen (Hz)", "f0_mean", ".1f"),
                             ("F0 CoV (%)", "f0_cov_pct", ".1f")]:
        row = f"  {label:<26s}"
        for r in results:
            row += f"{r['pitch'][key]:>15{fmt}}"
        lines.append(row)

    # DNSMOS
    lines.append("")
    lines.append("--- DNSMOS ---")
    for label, key in [("Overall MOS", "ovrl_mos"),
                        ("Signal MOS", "sig_mos"),
                        ("Background MOS", "bak_mos"),
                        ("P.808 MOS", "p808_mos")]:
        row = f"  {label:<26s}"
        for r in results:
            row += f"{r['dnsmos'][key]:>15.3f}"
        lines.append(row)

    lines.append("")
    lines.append("-" * W)

    # Ranking
    lines.append("")
    lines.append("CLASSEMENT TIMBRE UNIFORMITÉ :")

    # Score: higher similarity_mean is better, lower mfcc variance is better,
    # lower f0_cov is better
    scores = []
    for r in results:
        # Normalize each metric to [0,1] range across engines
        sim = r["speaker_embedding"]["similarity_mean"]
        mfcc_var = r["mfcc"]["variance_inter_seg"]
        f0_cov = r["pitch"]["f0_cov_pct"]
        dnsmos_ovrl = r["dnsmos"]["ovrl_mos"]
        scores.append((r["name"], sim, mfcc_var, f0_cov, dnsmos_ovrl))

    # Rank by composite: high sim + low mfcc_var + low f0_cov
    # Simple ranking: average of ranks
    n = len(scores)
    rank_sim = sorted(range(n), key=lambda i: -scores[i][1])  # higher is better
    rank_mfcc = sorted(range(n), key=lambda i: scores[i][2])   # lower is better
    rank_f0 = sorted(range(n), key=lambda i: scores[i][3])     # lower is better
    rank_dnsmos = sorted(range(n), key=lambda i: -scores[i][4])  # higher is better

    avg_ranks = []
    for i in range(n):
        r_sim = rank_sim.index(i) + 1
        r_mfcc = rank_mfcc.index(i) + 1
        r_f0 = rank_f0.index(i) + 1
        r_dns = rank_dnsmos.index(i) + 1
        avg = (r_sim + r_mfcc + r_f0 + r_dns) / 4
        avg_ranks.append((scores[i][0], avg, r_sim, r_mfcc, r_f0, r_dns))

    avg_ranks.sort(key=lambda x: x[1])
    for rank, (name, avg, rs, rm, rf, rd) in enumerate(avg_ranks, 1):
        lines.append(f"  {rank}. {name} (avg rank: {avg:.1f} — "
                     f"sim:{rs} mfcc:{rm} f0:{rf} dnsmos:{rd})")

    lines.append("")
    lines.append("=" * W)
    return "\n".join(lines)


def main():
    comp_dir = sys.argv[1] if len(sys.argv) > 1 else "/opt/sila/bench/comparison"
    comp_dir = Path(comp_dir)

    results = []
    for name, filename in ENGINES.items():
        mp4_path = comp_dir / filename
        if not mp4_path.exists():
            print(f"WARN: {mp4_path} not found, skipping {name}")
            continue
        r = analyze_one(name, str(mp4_path))
        results.append(r)

    if not results:
        print("No files found!")
        sys.exit(1)

    report = format_report(results)
    print(report)

    # Save JSON
    out_json = comp_dir / "timbre_analysis.json"
    with open(out_json, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nJSON: {out_json}")

    # Save text
    out_txt = comp_dir / "timbre_analysis.txt"
    with open(out_txt, "w") as f:
        f.write(report)
    print(f"Text: {out_txt}")


if __name__ == "__main__":
    main()
