# CosyVoice 3.0 — Parametres optimaux (SILA v3.0.0)

Preserve les parametres calibres par iteration empirique de V1 a V3 (mars 2026).
Ne pas modifier sans validation sur les 3 tests de reference.

## TTS Generation
- Model: CosyVoice 3.0 (0.5B), path: /opt/sila/models/cosyvoice3-0.5b
- Speed range: [0.95, 1.05] (P15 — au-dela, degradation perceptive)
- Seed: 42 (P11 — idempotence partielle, ADR-011 non-determinisme)
- Sample rate output: 24000 Hz (resample a 48000 dans le pipeline)

## Voice Profile P6
- Methode: multi-segment embedding (top 5 par confidence)
- Duree max reference: 30s (n_best=5, max_duration_s=30.0)
- Selection: segments entre 3-12s, confidence ASR > 0.7
- Stocke dans: voice_refs/spk_0_multi_ref.wav

## Duration Cascade (quality-first)
- P1: generation a speed=1.0
- Si ratio (TTS/budget) dans [0.85, 1.15]: garder P1 (PASS)
- Si ratio dans [0.95, 1.05]: garder P1 (stretch gerera)
- P2: si hors [0.85, 1.15]: regeneration a speed=max(0.95, min(1.05, ratio))
- P2 collapse retry: si P1 < 10% du budget et budget > 2000ms
- Pick P2 vs P1: le plus proche du budget
- P3 stretch: si TTS > budget, compute_stretch_ratio, max 1.10x (pyrubberband)
- P4 slowdown: si TTS < budget*0.85, ratio=TTS/budget, min ratio 0.60

## Rewrite Constraints
- Formule: calc_max_chars(budget_ms, lang, margin=0.90)
- Budget min rewrite: 7000ms (REWRITE_MIN_BUDGET_MS)
- Debit naturel EN: 12 chars/s
- Debit naturel FR: 10 chars/s
- Debit naturel ES: 10 chars/s
- Marge de securite: 0.90 (10%)
- Truncation TTS: max_chars = max(200, budget_ms * 0.02)

## Time-Stretch
- Outil: pyrubberband (src/media/rubberband.py)
- Max stretch: 1.10x (MAX_STRETCH_RATIO)
- Max slowdown: 0.60x (MIN_SLOWDOWN_RATIO)
- Seuil stretch: ratio > 1.01
- Seuil slowdown: TTS < budget * 0.85

## Post-processing
- Crossfade: 50ms (P8) — uniquement sur overlap reel entre segments
- Loudnorm: FFmpeg 2-pass, -16 LUFS (P10, EBU R128)
- Timebase: 48 kHz (P5)
- Ducking fond: -6 dB (quand Demucs actif)

## Quality Gates
- QC timing budget: +/-15%
- DNSMOS PASS: >= 3.0/5.0
- DNSMOS WARNING: >= 2.5/5.0
- DNSMOS FAIL: < 2.5/5.0

## Resultats de reference (CosyVoice, tag v3.0.0)
- test_002 (52s): QC 80%, DNSMOS 2.97, audit FAIL (tail silence)
- test_003 (356s): QC 41-62% (variance ADR-011), DNSMOS 3.05
- test_007 (62min): QC 48.5%, DNSMOS 2.84, ratio pipeline 1:1
