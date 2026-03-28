# Changelog

Toutes les modifications notables du projet SILA sont documentees ici.
Format base sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## [1.0.0] — 2026-03-28

### Features
- Pipeline sequentiel 11 phases : Ingest, Extract, ASR (WhisperX large-v3),
  Segmentation, Traduction (NLLB-200 3.3B CT2), TTS (CosyVoice3 0.5B),
  Assembly (crossfade 50ms + loudnorm -16 LUFS), QC timing, Export (remux MP4 + SRT)
- Manifeste JSON versionne avec suivi d'etat par phase
- CLI Click avec options : --input, --target-lang, --source-lang, --project-id,
  --data-dir, --from-stage, --verbose
- Reprise par etape (--from-stage) avec cache des artefacts intermediaires
- TTS 2-pass : Pass 1 a speed=1.0 mesure la duree reelle, Pass 2 regenere au
  speed exact, keep-closest selectionne le meilleur des deux passes
- Time-stretch rubberband jusqu'a 1.5x pour ajustement fin
- Estimation de vitesse TTS avec cap a 2.0x et detection de collapse relative
  (seuil 10% du budget)
- Resample TTS 24kHz vers 48kHz dans assembly (librosa)
- Troncature du texte TTS pour segments avec hallucinations ASR

### Tests valides
- test_001 : video finance YouTube, locuteur rapide (3.3 mots/s), 112s, 13 segments
- test_002 : video conference, locuteur pose (2.7 mots/s), 52s, 5 segments
- test_003 : Zeste de Science CNRS, narrateur (2.6 mots/s), 356s, 34 segments
- QC pass rate (±15%) :
  - Heuristique v1 : 23% / 20% / 12% (moyenne 15.4%)
  - TTS 2-pass :     15% / 60% / 44% (moyenne 38.5%)

### Bugs corriges
- 86a8579 : resample TTS 24kHz -> 48kHz dans assembly + troncature texte
- ab4cf30 : correction indentation troncature TTS
- dae7017 : estimation de vitesse TTS pour ajustement au budget
- 33f3e50 : correction base rate TTS (5.5 chars/s, max 2.5x)
- 037314e : retry TTS a speed=1.0 quand synthese collapse
- f0e6fd4 : TTS 2-pass (generer, mesurer, regenerer au speed exact)
- ad64b64 : keep-closest (garder P1 ou P2 le plus proche du budget)
- b5b69c6 : retry collapse P1, cap speed 2.0x, stretch 1.5x

### Limitations connues
- QC global a 38.5%, sous la cible de 85% du masterplan
- CosyVoice instable au-dessus de speed=2.0x (collapses frequents)
- Pas de separation vocale (Demucs desactive en V1)
- Pas de reecriture LLM contrainte en longueur
- Pas de diarisation multi-locuteur (mono-speaker V1, spk_0 hardcode)
- Seed TTS non effectif (resultats non reproductibles)
- Pas de monitoring VRAM intra-run
