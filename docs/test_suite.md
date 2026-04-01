# SILA — Suite de tests de référence

## Tests actifs (contenu cible YouTube)

Tous extraits de la même vidéo source test_007 (YouTube, 62 min).
Même locuteur, même style, même fond sonore.

| Test | Durée | Segments | Extrait | Usage |
|---|---|---|---|---|
| test_009 | 55s | 6 | Minutes 20:00-20:55 | Quick validation, benchmark HeyGen |
| test_008 | 5 min | 31 | Minutes 05:00-10:00 | Validation moyenne, calibrage |
| test_007 | 62 min | 369 | Vidéo complète | Scale test, production |

## Tests historiques (conservés, plus utilisés en référence)

| Test | Durée | Type | Raison du retrait |
|---|---|---|---|
| test_002 | 52s | Conférence | Contenu non représentatif, seulement 5 segments |
| test_003 | 356s | Science (Zeste de Science) | Locuteur/style différent du contenu cible |

## Résultats de référence

### MOSS-TTS v3.2 (engine par défaut prévu)

| Test | QC ±15% | DNSMOS | Coverage | LRA | Audit |
|---|---|---|---|---|---|
| test_009 | 83% (5/6) | 3.25 | 92.9% | 5.5 | PASS (8/8) |
| test_008 | 100% (31/31) | 3.31 | 95.5% | 5.1 | FAIL (TP -0.9) |
| test_007 | 99.5% | 3.25 | 95% | ? | ? |

### CosyVoice (fallback)

| Test | QC ±15% | DNSMOS | Coverage |
|---|---|---|---|
| test_007 | 48.5% | 2.84 | 62% |

### HeyGen (benchmark concurrent)

| Test | DNSMOS | Coverage | LRA | F0 |
|---|---|---|---|---|
| test_002 | 2.89 | 93.5% | 16.7 | 105 Hz |
