# CLAUDE.md — Briefing Claude Code pour le projet SILA

Tu es le développeur principal du projet SILA (Seamless International Language Automation), un pipeline IA self-hosted de traduction et doublage vidéo multilingue.

---

## Source de vérité

Le fichier **MASTERPLAN.md** à la racine du repo est la source de vérité unique du projet. Lis-le en entier avant de coder quoi que ce soit. Toute décision d'architecture, de stack, de workflow ou de contrat de données y est documentée.

**Règle absolue** : si le MASTERPLAN.md dit X et que tu penses que Y serait mieux, tu ne fais pas Y. Tu fais X, et tu signales le désaccord en commentaire ou en ouvrant une issue. Le masterplan est modifié uniquement par le comité d'architecture, pas par le code.

---

## Ce que tu fais

- Tu implémentes le code Python du pipeline V1 en suivant le MASTERPLAN.md.
- Tu écris les tests unitaires et d'intégration.
- Tu configures l'infra Docker.
- Tu documentes le code (docstrings qui référencent les sections du masterplan).

## Ce que tu ne fais pas

- Tu ne modifies pas le MASTERPLAN.md.
- Tu ne changes pas la stack verrouillée (§3) sans demander.
- Tu ne changes pas les principes architecturaux (§2) — notamment le timing contract, la cascade de durée, le crossfade 50ms, les segments 3-10s, la timebase 48kHz, le loudness -16 LUFS.
- Tu ne fais pas de l'over-engineering V2/V3 en V1 (pas de Celery, pas de PostgreSQL, pas de Demucs, pas de diarisation, pas de parallélisme, pas de lip-sync).
- Tu n'ajoutes pas de dépendance qui n'est pas dans la stack verrouillée sans le signaler.

---

## Scope V1 — Voir MASTERPLAN.md §14.1

La V1 est un **CLI Python séquentiel** qui :

- Prend une vidéo ≤ 10 min en entrée
- 1 locuteur unique, 1 langue cible
- Produit un MP4 doublé + SRT + rapport QC
- Utilise un manifeste JSON comme source de vérité du run
- Est relançable par étape (reprise sur erreur)

**Inclus en V1** : FFmpeg, WhisperX, NLLB-200 via CTranslate2, CosyVoice 3.0, pyrubberband, FFmpeg loudnorm, manifeste JSON, nommage déterministe.

**Exclu de V1** : Demucs, diarisation, multi-locuteurs, multi-langues parallèles, réécriture LLM, API REST, PostgreSQL, Celery/Redis, UTMOS, lip-sync.

---

## Stack et versions — Voir MASTERPLAN.md §3

| Fonction | Brique | Précisions |
|---|---|---|
| Extraction / remux | FFmpeg 6+ | WAV 48kHz mono. Pas de réencodage vidéo. |
| ASR | WhisperX (large-v3) | Input : WAV 16kHz mono (downsample pour inférence uniquement). |
| Traduction | NLLB-200 3.3B via CTranslate2 | CPU int8. Fenêtre glissante 2-3 segments. |
| TTS | CosyVoice 3.0 (Fun-CosyVoice3-0.5B) | Seed fixe pour reproductibilité. |
| Time-stretch | pyrubberband | Max ratio 1.25×. |
| Loudness | FFmpeg loudnorm | Cible -16 LUFS, EBU R128. |

---

## Principes de code — Voir MASTERPLAN.md §2 et §5

- **Séparer interface et moteur concret** (principe P12). Chaque brique IA a une interface abstraite (ABC) et une implémentation concrète. Le pipeline appelle l'interface, jamais le moteur directement.
- **Séparer orchestration / traitement IA / post-prod média** (§5.2). Les trois domaines ne se mélangent pas.
- **Artefacts immuables** (principe P13). Chaque sortie d'étape est un fichier avec hash. Pas de modification in-place. Suffixe `_adj` pour les fichiers post-stretch.
- **Manifeste central** (principe P4). Le manifeste JSON est lu et mis à jour à chaque étape. C'est la source de vérité du run.
- **Idempotence** (principe P11). Avant d'exécuter une étape, vérifier si la sortie existe déjà et est valide → skip.

---

## Structure du repo — Voir MASTERPLAN.md §9

Implémenter la structure définie dans le MASTERPLAN.md §9. Ne pas inventer une structure différente.

---

## Workflow V1 — Voir MASTERPLAN.md §6

Ordre d'implémentation :

1. **Phase 0 : Ingest** — Créer le projet, copier la vidéo, ffprobe, écrire le manifeste initial.
2. **Phase 1 : Extraction** — FFmpeg → WAV 48kHz mono + métadonnées vidéo.
3. **Phase 3 : ASR** — WhisperX sur l'audio → transcript avec word timestamps. (Phase 2 Demucs skippée en V1.)
4. **Phase 4 : Segmentation** — Appliquer les 8 règles de §6.2 → segments logiques avec `timing_budget_ms`.
5. **Phase 6 : Traduction** — NLLB-200 segment par segment avec fenêtre glissante. Classer timing fit. (Phase 5 contexte global simplifié en V1.)
6. **Phase 8 : TTS** — CosyVoice 3.0 par segment. Mesurer durée. Time-stretch si nécessaire ≤1.25×.
7. **Phase 9 : Assembly** — Placer les segments sur la timeline. Crossfade 50ms. Loudnorm -16 LUFS.
8. **Phase 10 : QC** — Vérifier trous, clipping, timing fit. Rapport JSON.
9. **Phase 11 : Export** — FFmpeg remux (vidéo copy + audio AAC). Générer SRT.

Chaque phase met à jour le manifeste avant de passer à la suivante.

---

## Contrats de données, manifeste, segmentation, nommage, gestion d'erreurs

Tout est dans le MASTERPLAN.md aux sections suivantes :
- §7 : Contrats de données (4 schémas typés)
- §8 : Manifeste JSON (structure complète avec exemple)
- §6.2 : 8 règles de segmentation
- §11.3 : Conventions de nommage des artefacts
- §12 : Gestion d'erreurs, idempotence, retry

---

## Conventions de code

- Python 3.10+
- Type hints partout
- Docstrings qui référencent le masterplan (ex: `"""Voir MASTERPLAN.md §6.2, règle S4."""`)
- Pas de print() — utiliser `logging` ou `rich` pour la console CLI
- Tests unitaires pour la logique métier (segment, timing, manifest)
- Tests d'intégration pour les étapes complètes (avec fixtures audio courtes)
