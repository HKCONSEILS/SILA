# MASTERPLAN — Pipeline IA de Traduction & Doublage Vidéo Multilingue

**Nom de code** : `SILA — Seamless International Language Automation`
**Version du document** : 1.1.0
**Date** : 2026-03-23
**Statut** : Draft — En cours de validation
**Auteur** : Comité d'architecture (4 experts)
**Licence projet** : À définir (self-hosted, usage interne ou commercial)

---

## Table des matières

- [0. Comment utiliser ce document](#0-comment-utiliser-ce-document)
- [1. Vision et périmètre](#1-vision-et-périmètre)
- [2. Principes fondateurs](#2-principes-fondateurs)
- [3. Stack technologique verrouillée](#3-stack-technologique-verrouillée)
- [4. Briques explicitement rejetées](#4-briques-explicitement-rejetées)
- [5. Architecture logicielle](#5-architecture-logicielle)
- [6. Workflow détaillé — DAG du pipeline](#6-workflow-détaillé--dag-du-pipeline)
- [7. Contrats de données](#7-contrats-de-données)
- [8. Manifeste central](#8-manifeste-central)
- [9. Structure du monorepo](#9-structure-du-monorepo)
- [10. Architecture matérielle](#10-architecture-matérielle)
- [11. Infrastructure et déploiement](#11-infrastructure-et-déploiement)
- [12. Stratégie de gestion d'erreurs et reprise](#12-stratégie-de-gestion-derreurs-et-reprise)
- [13. KPI et quality gates](#13-kpi-et-quality-gates)
- [14. Roadmap V1 / V2 / V3](#14-roadmap-v1--v2--v3)
- [15. Veille technologique et repos à surveiller](#15-veille-technologique-et-repos-à-surveiller)
- [16. Risques identifiés et mitigations](#16-risques-identifiés-et-mitigations)
- [17. Glossaire](#17-glossaire)
- [18. Historique des décisions (ADR)](#18-historique-des-décisions-adr)

---

## 0. Comment utiliser ce document

Ce masterplan est la **source de vérité unique** du projet. Toute décision d'architecture, de stack ou de workflow doit y être référencée ou en être dérivée.

**Règles de gouvernance :**
- Toute modification de ce document doit être versionnée (Git) avec un message de commit explicite.
- Les décisions verrouillées (marquées ✅) ne peuvent être modifiées que par une revue explicite du comité, avec justification documentée dans la section ADR.
- Les sections marquées 🔄 sont en évaluation et seront verrouillées avant l'implémentation de la version concernée.
- Ce document est en Markdown pour être versionné dans le repo, relu en PR, et annoté par les contributeurs.

---

## 1. Vision et périmètre

### 1.1 Objectif

Construire un pipeline self-hosted, modulaire et industrialisable, capable de prendre en entrée une vidéo longue (jusqu'à 1h+), d'en produire des versions doublées dans plusieurs langues cibles, avec une voix clonée cohérente, un alignement temporel contrôlé, et un rendu audio crédible.

### 1.2 Ce que le système fait

- Extraire l'audio d'une vidéo source.
- Séparer la voix du fond sonore (musique, ambiance, SFX).
- Transcrire la parole avec timestamps mot-à-mot et identification des locuteurs.
- Segmenter intelligemment le contenu parlé en unités de traduction/TTS.
- Traduire chaque segment vers N langues cibles avec contexte et glossaire.
- Réécrire les traductions trop longues pour respecter le timing original.
- Cloner la voix de chaque locuteur et générer l'audio cible par segment et par langue.
- Ajuster la durée de chaque segment TTS pour respecter le slot temporel source.
- Mixer l'audio TTS avec le fond sonore original, normaliser le loudness.
- Remuxer l'audio final avec la piste vidéo source (sans réencodage vidéo).
- Exporter un fichier MP4 par langue cible.

### 1.3 Ce que le système ne fait pas (hors périmètre)

- Temps réel ou streaming live.
- Traitement vidéo (réencodage, modification des frames, effets visuels) — sauf lip-sync optionnel en V3.
- Sous-titrage seul (c'est un sous-produit, pas l'objectif).
- Qualité "studio broadcast" universelle — l'objectif est "convaincant sur un périmètre de langues et contenus défini".
- Lip-sync en V1-V2 (hors chemin critique, branche séparée en V3).

### 1.4 Cadre par défaut

- Self-hosted, local ou infra privée.
- Vidéos longues (1h+) comme cas normal, pas comme exception.
- Pipeline relançable, batchable, parallélisable et auditable.
- Approche réaliste et industrialisable, pas démo théorique.
- Pas de SaaS managé sauf nécessité démontrée.

---

## 2. Principes fondateurs

### 2.1 Principes architecturaux verrouillés

| # | Principe | Description |
|---|---|---|
| P1 | **Timing contract** | Chaque segment porte un `timing_budget_ms` dès la segmentation. Chaque étape en aval respecte ce budget. Contrôle de durée en amont, pas en aval. |
| P2 | **Cascade de durée** | Segmenter → budget → traduire avec contrainte → réécrire si nécessaire → ajuster prosodie TTS → time-stretch ≤1.25× en dernier recours. |
| P3 | **Tronc commun** | Les étapes 1-4 (extraction → Demucs → WhisperX → segmentation) sont exécutées une seule fois, quel que soit le nombre de langues cibles. |
| P4 | **Manifeste central** | Un JSON par projet, source de vérité pour l'état, la reprise, le cache et l'audit. |
| P5 | **Timebase 48 kHz** | Référence maître. Downsampling 16 kHz uniquement pour l'inférence Whisper. |
| P6 | **Profil voix global** | Embedding moyen calculé sur les 10 meilleurs segments par locuteur, pas un seul extrait. |
| P7 | **Mapping speaker** | `speaker_id → voice_profile → target_voice_id → target_lang`. |
| P8 | **Crossfade 50 ms** | Entre segments TTS finaux. Pas 200-300 ms. |
| P9 | **Segments 3s-10s** | Durée cible pour le TTS. Hard cap à 12s. |
| P10 | **Loudness -16 LUFS** | Cible web/streaming, norme EBU R128. |
| P11 | **Idempotence** | Chaque étape vérifiable. Même entrée + même config → skip si sortie valide. Le TTS nécessite en plus un seed fixe. |
| P12 | **Interchangeabilité** | Séparer interface de tâche (ASR, MT, TTS, rewrite) et moteur concret. Chaque moteur est swappable sans casser le pipeline. |
| P13 | **Artefacts immuables** | Chaque sortie d'étape est un fichier immuable avec hash. Pas de modification in-place. |
| P14 | **Vidéo source intouchée** | La piste vidéo n'est jamais réencodée avant le remux final. |

### 2.2 Séparations conceptuelles fondamentales

Toujours distinguer :

| Concept A | ≠ | Concept B |
|---|---|---|
| Segmentation physique (chunks de calcul) | | Segmentation logique (segments métier canoniques) |
| Orchestration (état, DAG, retries) | | Traitement IA (production de contenu) |
| Traitement IA | | Post-production média (timeline, mix, loudness) |
| Interface de tâche (ex: "traduire un segment") | | Moteur concret (ex: NLLB-200 3.3B) |
| Audio d'analyse (16 kHz, mono) | | Audio de travail (48 kHz, mono) |
| Audio de travail | | Audio master (48 kHz, mixé, normalisé) |

### 2.3 Niveaux de contexte à maintenir

Malgré le découpage en segments, le pipeline doit maintenir du contexte à trois niveaux :

1. **Narratif** : résumé scène/chapitre, sujet global, progression du contenu.
2. **Locuteur** : registre de langue, identité vocale, style d'élocution.
3. **Terminologique** : glossaire projet, noms propres, acronymes, entités nommées.

---

## 3. Stack technologique verrouillée

### 3.1 Briques retenues

| Fonction | Brique | Licence | VRAM estimée | Statut |
|---|---|---|---|---|
| Extraction / remux | FFmpeg 6+ | LGPL 2.1 | 0 (CPU) | ✅ Verrouillé |
| Séparation vocale | Demucs v4 (htdemucs_ft) | MIT | ~4 Go | ✅ Verrouillé — repo archivé, dépendance figée |
| Transcription + alignment + diarisation | WhisperX (large-v3 + pyannote 3.1). Décomposition V2. Qwen3-ASR et Voxtral Mini Transcribe V2 à évaluer en V2. | BSD-4 | ~8 Go | ✅ Verrouillé |
| Traduction | NLLB-200 3.3B via CTranslate2 | ⚠️ CC-BY-NC 4.0 (poids) + MIT (runtime) | ~3 Go (int8 CPU) | ✅ Verrouillé sous réserve licence |
| Traduction (alt. commerciale) | MADLAD-400 (Google) via CTranslate2 | Apache 2.0 | ~3 Go (int8 CPU) | 🔄 Si usage commercial |
| Réécriture contrainte (V2) | Mistral Small 3.2 24B (Unsloth Dynamic 2.0 Q4_K_M) | Apache 2.0 | ~15 Go | ✅ Retenu V2 |
| Réécriture contrainte (fallback) | Ministral 3 8B Instruct (Unsloth Dynamic 2.0 Q4_K_M) | Apache 2.0 | ~5 Go | ✅ Retenu V2 |
| TTS / voice cloning (principal) | CosyVoice 3.0 (Fun-CosyVoice3-0.5B) | Apache 2.0 | ~4 Go | ✅ Verrouillé |
| TTS / voice cloning (alternatif) | Qwen3-TTS 1.7B | Apache 2.0 | ~6 Go | ✅ Verrouillé |
| Time-stretching | pyrubberband (wrapper librubberband) | MIT | 0 (CPU) | ✅ Verrouillé |
| Normalisation loudness | FFmpeg loudnorm (EBU R128). pyloudnorm en V2. | — | 0 (CPU) | ✅ Verrouillé |
| Orchestration | Script séquentiel (V1), Celery + Redis (V2), Temporal (V3) | BSD/MIT | 0 (CPU) | ✅ Verrouillé |
| Qualité audio estimée | UTMOS | — | ~1 Go | ✅ Retenu |
| Base de données | Fichier JSON (V1), PostgreSQL 16 JSONB (V2+) | PostgreSQL Licence | 0 (CPU) | ✅ Verrouillé |
| Stockage objet | Filesystem local (V1), MinIO (V3) | AGPL-3.0 / commercial | 0 | ✅ Verrouillé |
| API | Aucune (V1), FastAPI (V2+) | MIT | 0 (CPU) | ✅ Verrouillé |

### 3.2 Note licence NLLB-200

Les poids NLLB-200 de Meta sont sous **CC-BY-NC 4.0** (non-commercial uniquement). CTranslate2 est MIT mais ça ne change pas la licence des poids.

- **Usage interne / non-commercial** → NLLB-200 3.3B. Meilleur choix technique.
- **Usage commercial** → **MADLAD-400** (Google, Apache 2.0, 450+ langues). Performances légèrement inférieures sur les langues à basse ressource.
- **Action** : faire valider par un juriste avant la mise en production commerciale.

### 3.3 Détail des briques TTS

#### CosyVoice 3.0 (principal)

- **Modèle** : Fun-CosyVoice3-0.5B-2512 (recommandé) ou 1.5B
- **Langues** : 9 (zh, en, ja, ko, de, es, fr, it, ru) + 18 dialectes chinois
- **Features** : Zero-shot cross-lingual cloning, pronunciation inpainting (phonèmes), instruction-based control (vitesse, émotion), streaming bi-directionnel
- **Accélération** : vLLM 0.11+, TensorRT-LLM (4× speedup)
- **Taux d'erreur** : CER 0.81%, speaker similarity 77.4%
- **Sample rate** : 22.05 kHz en interne → resample 48 kHz en sortie

#### Qwen3-TTS (alternatif, à benchmarker)

- **Modèle** : Qwen3-TTS-12Hz-1.7B-Base (clonage) + 1.7B-CustomVoice (voix prédéfinies)
- **Langues** : 10 (zh, en, ja, ko, de, fr, ru, pt, es, it)
- **Features** : Clonage 3s de référence, voice design par description textuelle, dual-track LM, génération longue (jusqu'à 10 min)
- **Taux d'erreur** : WER moyen 1.835% sur 10 langues, speaker similarity 0.789
- **Décision** : Benchmarker CosyVoice vs Qwen3-TTS sur golden set interne avant V2. Inversion possible.

### 3.4 Détail du LLM de réécriture (V2)

#### Mistral Small 3.2 24B (principal)

- **Modèle** : Mistral-Small-3.2-24B-Instruct-2506
- **Quantification** : Unsloth Dynamic 2.0 Q4_K_M GGUF (~15 Go VRAM)
- **Serving** : llama.cpp, vLLM, ou Ollama
- **Features** : Multilingue 40+ langues, function calling, JSON output structuré, context 128k
- **Tâche** : Recevoir `(texte_traduit, max_chars, timing_budget_ms, contexte, glossaire)` → produire une variante plus courte respectant le timing.

#### Ministral 3 8B (fallback léger)

- **Modèle** : Ministral-3-8B-Instruct
- **Quantification** : Unsloth Dynamic 2.0 Q4_K_M (~5 Go VRAM)
- **Usage** : Quand la VRAM est occupée par le TTS (cohabitation sur GPU unique)

---

## 4. Briques explicitement rejetées

| Brique | Raison du rejet |
|---|---|
| XTTS v2 (Coqui) | Licence CPML non-commercial. Coqui fermé. |
| Wav2Lip | Licence non-commerciale. Daté (2020). Qualité insuffisante. |
| M2M100 | Remplacé par NLLB-200 (supérieur sur toutes les paires). |
| Voicebox (jamiepine) | Wrapper communautaire sans poids officiels Meta. |
| OpenVoice V2 seul | Ne clone que la couleur tonale, pas accent/émotion. Fallback léger uniquement. |
| Orpheus TTS | 3B (Llama backbone). Multilingue en "research preview" seulement. Pas production-ready hors anglais. |
| Fish Speech | Moins mature que CosyVoice/Qwen3-TTS en cross-lingual. |
| SeamlessM4T | Speech-to-speech. Mauvais outil pour un pipeline text-to-text segmenté. |
| Mistral Small 4 (119B MoE) | 242 Go disque, 6B actifs/token. Nécessite 2+ GPU même quantifié. Incompatible contrainte <24 Go VRAM en V1-V2. À surveiller pour V3 si infra multi-GPU dédiée. Apache 2.0, reasoning + vision + coding unifié. |
| Voxtral Small 24B | ~55 Go VRAM en bf16, nécessite 2× GPU. Trop lourd pour single 3090/4090. Voxtral Mini/Realtime 4B est le bon candidat pour SILA (≤16 Go VRAM). |
| vLLM + Llama 3 70B (traduction) | Surdimensionné. NLLB-200 supérieur en traduction pure et 10× plus rapide. |
| Triton Inference Server | Over-engineering V1-V2. vLLM/llama.cpp suffisent. |
| RabbitMQ | Plus complexe que Redis pour notre cas. Celery + Redis suffit en V2. |
| Kubernetes en V1-V2 | Over-engineering. Docker Compose suffit. |
| Architecture microservices en V1 | Over-engineering. Fonctions modulaires + Celery suffisent. |

---

## 5. Architecture logicielle

### 5.1 Vue d'ensemble par version

#### V1 — Script séquentiel

```
CLI (Python)
  └── Pipeline séquentiel
        ├── extract_audio()
        ├── transcribe()        → WhisperX
        ├── segment()           → Règles métier Python
        ├── translate()         → NLLB-200 via CTranslate2
        ├── generate_tts()      → CosyVoice 3.0
        ├── adjust_timing()     → pyrubberband
        ├── assemble_audio()    → FFmpeg
        ├── normalize()         → FFmpeg loudnorm
        └── export()            → FFmpeg remux
  └── Manifeste JSON (lecture/écriture à chaque étape)
  └── Filesystem local (artefacts)
```

#### V2 — API + Workers

```
┌─────────────────┐     ┌──────────────┐
│  FastAPI         │────▶│  PostgreSQL   │
│  (API REST)      │     │  (JSONB)      │
└────────┬────────┘     └──────────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│  Celery          │────▶│  Redis        │
│  (Orchestrateur) │     │  (Broker)     │
└────────┬────────┘     └──────────────┘
         │
    ┌────┴────┬──────────┬──────────┬──────────┐
    ▼         ▼          ▼          ▼          ▼
 Worker    Worker     Worker     Worker     Worker
 Media     Speech     Translate  TTS        PostProd
 (CPU)     (GPU)      (CPU)      (GPU)      (CPU)
```

#### V3 — Orchestration durable

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────┐
│  FastAPI + UI    │────▶│  PostgreSQL   │     │  Prometheus   │
│  (Review UI)     │     │  (JSONB)      │     │  + Grafana    │
└────────┬────────┘     └──────────────┘     └──────────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐     ┌──────────────┐
│  Temporal        │────▶│  Redis        │     │  MinIO / S3   │
│  (Orchestrateur) │     │  (Cache)      │     │  (Stockage)   │
└────────┬────────┘     └──────────────┘     └──────────────┘
         │
    ┌────┴────┬──────────┬──────────┬──────────┬──────────┐
    ▼         ▼          ▼          ▼          ▼          ▼
 Worker    Worker     Worker     Worker     Worker     Worker
 Media     Speech     Translate  TTS        PostProd   LipSync
```

### 5.2 Frontières entre domaines

| Domaine | Responsabilité | Ne touche JAMAIS à |
|---|---|---|
| **Orchestration** | État global, DAG, retries, timeouts, reprise partielle | Fichiers audio/vidéo directement |
| **Traitement IA** | ASR, diarisation, traduction, TTS, réécriture | État global du run, décisions de retry |
| **Post-production média** | Timeline, placement audio, loudness, mix, crossfade, remux | Logique métier (traduction, segmentation) |

### 5.3 Interfaces de tâche (contrats)

Chaque brique IA est encapsulée derrière une interface abstraite :

```
ASR_Interface:
  input:  audio_path (WAV 16kHz mono)
  output: TranscriptResult { words[], speakers[], language }

MT_Interface:
  input:  text, source_lang, target_lang, context[], glossary{}
  output: TranslationResult { text, estimated_chars, confidence }

Rewriter_Interface:
  input:  text, target_lang, max_chars, timing_budget_ms, context
  output: RewriteResult { text, char_count, fit_status }

TTS_Interface:
  input:  text, voice_profile, target_lang, target_duration_ms, seed
  output: TTSResult { audio_path, duration_ms, sample_rate }

QC_Interface:
  input:  audio_path, reference_duration_ms
  output: QCResult { utmos_score, duration_ms, timing_delta_ms, flags[] }
```

---

## 6. Workflow détaillé — DAG du pipeline

### 6.1 Vue d'ensemble du DAG

```
Phase 0: INGEST
  └── Créer projet, uploader vidéo, ffprobe, écrire manifeste initial

Phase 1: EXTRACTION (séquentiel, une seule fois)
  ├── 1.1  FFmpeg → audio source WAV 48kHz mono
  └── 1.2  FFmpeg → métadonnées vidéo (fps, résolution, durée, codecs, chapitres)

Phase 2: SÉPARATION VOCALE (séquentiel, une seule fois) [V2+]
  └── 2.1  Demucs v4 → stems : voice.wav, music.wav, sfx.wav

Phase 3: ANALYSE SPEECH (séquentiel, une seule fois)
  ├── 3.1  WhisperX ASR sur voice.wav (ou audio.wav en V1) → transcript brut
  ├── 3.2  Alignement mot-à-mot (wav2vec2 via WhisperX)
  └── 3.3  Diarisation (pyannote 3.1 via WhisperX) [V2+]

Phase 4: SEGMENTATION LOGIQUE (séquentiel, une seule fois)
  ├── 4.1  Réconciliation transcript (fusion chunks, nettoyage overlaps techniques)
  ├── 4.2  Attribution speaker IDs canoniques
  ├── 4.3  Segmentation métier (règles ci-dessous)
  └── 4.4  Attribution timing_budget_ms par segment

Phase 5: CONTEXTE GLOBAL (séquentiel, une seule fois)
  ├── 5.1  Résumé narratif global / par chapitre
  ├── 5.2  Extraction entités nommées
  ├── 5.3  Constitution glossaire projet
  └── 5.4  Extraction profil voix par locuteur (embedding moyen sur top-10 segments)

            ══════════════════════════════════════
            ║  FAN-OUT : × N langues cibles       ║
            ══════════════════════════════════════

Phase 6: TRADUCTION (parallèle par langue, séquentiel par segment pour le contexte)
  ├── 6.1  Traduire segment par segment avec fenêtre glissante (2-3 segments)
  ├── 6.2  Injecter glossaire + contexte narratif
  └── 6.3  Estimer durée cible → classer : fit_ok | rewrite_needed | review_required

Phase 7: RÉÉCRITURE CONTRAINTE [V2+] (parallèle par segment)
  └── 7.1  Si rewrite_needed : LLM local → variante courte respectant timing_budget_ms

Phase 8: TTS / VOICE CLONING (parallèle par segment × langue)
  ├── 8.1  Générer audio TTS (CosyVoice / Qwen3-TTS)
  ├── 8.2  Mesurer durée réelle
  ├── 8.3  Si écart > seuil : time-stretch ≤1.25× (pyrubberband)
  └── 8.4  Si stretch > 1.25× → review_required

Phase 9: ASSEMBLY AUDIO (séquentiel par langue)
  ├── 9.1  Placer chaque segment sur la timeline cible
  ├── 9.2  Crossfade 50ms inter-segments
  ├── 9.3  Mixer piste TTS + piste musique + piste SFX [V2+]
  ├── 9.4  Ducking automatique sous la voix (-6dB) [V2+]
  └── 9.5  Normalisation loudness -16 LUFS (EBU R128)

Phase 10: QC AUTOMATIQUE (parallèle par langue)
  ├── 10.1  Vérifier trous de timeline (gaps > 500ms non justifiés)
  ├── 10.2  Vérifier clipping, true peak, loudness
  ├── 10.3  Vérifier timing fit par segment
  ├── 10.4  UTMOS par segment [V2+]
  └── 10.5  Tagger segments problématiques → review_required

Phase 11: EXPORT (parallèle par langue)
  ├── 11.1  FFmpeg remux : piste audio finale + piste vidéo source (copy, pas de réencodage)
  ├── 11.2  Générer SRT/VTT synchronisé
  └── 11.3  Générer rapport QC JSON

Phase 12: LIP-SYNC [V3, optionnel, hors chemin critique]
  └── 12.1  Uniquement sur segments flaggés face-caméra avec désync visible
```

### 6.2 Règles de segmentation logique

| # | Règle | Détail |
|---|---|---|
| S1 | Pas de mélange de locuteurs | Un segment = un seul `speaker_id` |
| S2 | Couper sur pause + ponctuation | Privilégier les coupures sur pause > 400ms coïncidant avec une ponctuation forte (. ? !) |
| S3 | Durée nominale 4-8s | Cœur de distribution visé |
| S4 | Hard cap 10s | Au-delà, forcer une coupe sur ponctuation faible (,) ou pause > 200ms |
| S5 | Minimum 3s | En dessous, fusionner avec le segment adjacent du même locuteur |
| S6 | Seuil de pause adaptatif | Si le contenu est dense (débit rapide), réduire le seuil à 300ms |
| S7 | Overlaps = cas spécial | Segments avec overlap de locuteurs → flag `overlap: true`, traitement dédié |
| S8 | Contexte conservé | Chaque segment porte `context_left` (2-3 segments avant) et `context_right` (1-2 segments après) |

### 6.3 Séquentiel vs parallélisable

| Séquentiel obligatoire | Parallélisable |
|---|---|
| Extraction audio | Traduction par langue (indépendant) |
| Séparation vocale (Demucs) | TTS par segment × langue (indépendant) |
| ASR + alignement + diarisation | Réécriture par segment (indépendant) |
| Segmentation logique | QC par segment ou par langue |
| Construction contexte global | Exports finaux par langue |
| Assembly audio par langue (séquentiel interne) | |

**Règle d'or** : tout ce qui nécessite une vision globale du média doit passer par une phase de consolidation centrale avant le fan-out.

---

## 7. Contrats de données

### 7.1 Transcript canonique (sortie Phase 3)

```
word_id         : int           — identifiant unique du mot
chunk_id        : int           — chunk technique d'origine
speaker_id      : str           — "spk_0", "spk_1", ...
source_lang     : str           — code ISO 639-1
start_ms        : int           — début en ms depuis le début de la vidéo
end_ms          : int           — fin en ms
text            : str           — le mot transcrit
confidence      : float         — 0.0 à 1.0
is_overlap      : bool          — true si chevauchement avec un autre locuteur
sentence_id     : int           — regroupement en phrases
```

### 7.2 Segments logiques (sortie Phase 4)

```
segment_id          : str       — "seg_0001"
speaker_id          : str       — "spk_0"
start_ms            : int       — début exact en ms
end_ms              : int       — fin exacte en ms
duration_ms         : int       — end_ms - start_ms
timing_budget_ms    : int       — budget alloué (peut différer de duration_ms)
source_text         : str       — texte source transcrit
source_lang         : str       — langue source détectée
context_left        : str       — 2-3 segments précédents concaténés
context_right       : str       — 1-2 segments suivants
segment_type        : str       — "speech" | "overlap" | "silence" | "music"
words               : list      — mots avec timestamps individuels
review_flags        : list      — ["low_confidence", "overlap", ...]
```

### 7.3 Traductions (sortie Phase 6)

```
segment_id          : str
target_lang         : str       — code ISO 639-1 cible
translated_text     : str       — texte traduit
alt_text_short      : str|null  — variante courte (si rewrite_needed)
estimated_chars     : int       — nombre de caractères
estimated_duration_ms : int     — durée TTS estimée
compression_ratio   : float     — ratio chars cible / chars source
timing_fit_status   : str       — "fit_ok" | "rewrite_needed" | "review_required"
glossary_hits       : list      — termes du glossaire utilisés
mt_engine           : str       — "nllb-200-3.3b" | "madlad-400"
mt_model_version    : str       — version exacte du modèle
```

### 7.4 Sorties TTS (sortie Phase 8)

```
segment_id          : str
target_lang         : str
voice_profile_id    : str
tts_engine          : str       — "cosyvoice-3.0-0.5b" | "qwen3-tts-1.7b"
tts_model_version   : str
audio_uri           : str       — chemin vers le WAV généré
duration_ms         : int       — durée réelle du TTS
timing_budget_ms    : int       — budget alloué
timing_delta_ms     : int       — écart (positif = trop long)
stretch_applied     : bool
stretch_ratio       : float     — 1.0 si pas de stretch
final_audio_uri     : str       — chemin après stretch si applicable
seed                : int       — seed utilisé pour reproductibilité
utmos_score         : float|null — score qualité (V2+)
```

---

## 8. Manifeste central

### 8.1 Principes

- **V1** : fichier JSON sur filesystem local. Nommage : `{project_id}/manifest.json`.
- **V2+** : PostgreSQL JSONB comme index de consultation rapide. Le JSON reste la source de vérité conceptuelle.
- Versionné : chaque mise à jour incrémente `manifest_version`.
- Chaque worker lit le manifeste, fait son travail, met à jour le manifeste.
- Nommage déterministe des artefacts : `{project_id}/{stage}/{lang}/seg_{index:04d}.wav`.

### 8.2 Structure du manifeste V1

```json
{
  "manifest_version": 1,
  "pipeline_version": "0.1.0",
  "created_at": "2026-03-22T14:30:00Z",
  "updated_at": "2026-03-22T15:12:44Z",

  "project": {
    "project_id": "proj_20260322_001",
    "status": "processing",
    "source_video": "data/projects/proj_20260322_001/source/input.mp4",
    "source_lang": "fr",
    "target_langs": ["en"],
    "duration_ms": 600000
  },

  "source_metadata": {
    "fps": 29.97,
    "resolution": "1920x1080",
    "codec_video": "h264",
    "codec_audio": "aac",
    "sample_rate": 48000,
    "chapters": []
  },

  "config": {
    "tts_engine": "cosyvoice-3.0-0.5b",
    "mt_engine": "nllb-200-3.3b-ct2",
    "max_segment_duration_ms": 10000,
    "preferred_segment_duration_ms": 6000,
    "pause_split_threshold_ms": 400,
    "crossfade_ms": 50,
    "max_stretch_ratio": 1.25,
    "loudness_target_lufs": -16,
    "tts_seed": 42
  },

  "speakers": {
    "spk_0": {
      "label": null,
      "voice_ref_uri": "data/projects/.../voice_refs/spk_0_ref.wav",
      "voice_embedding_uri": "data/projects/.../voice_refs/spk_0_embedding.pt",
      "target_voices": {
        "en": {
          "voice_profile_id": "spk_0_en",
          "tts_engine": "cosyvoice-3.0-0.5b"
        }
      }
    }
  },

  "stages": {
    "extract":      { "status": "completed", "started_at": "...", "finished_at": "..." },
    "demucs":       { "status": "skipped",   "reason": "V1 — pas de séparation vocale" },
    "asr":          { "status": "completed", "started_at": "...", "finished_at": "..." },
    "segmentation": { "status": "completed", "segments_count": 87 },
    "context":      { "status": "completed" },
    "translate_en": { "status": "running",   "segments_done": 45, "segments_total": 87 },
    "tts_en":       { "status": "pending" },
    "assembly_en":  { "status": "pending" },
    "qc_en":        { "status": "pending" },
    "export_en":    { "status": "pending" }
  },

  "segments": [
    {
      "segment_id": "seg_0001",
      "speaker_id": "spk_0",
      "start_ms": 1200,
      "end_ms": 7650,
      "duration_ms": 6450,
      "timing_budget_ms": 6450,
      "source_text": "Bonjour à tous et bienvenue dans cette présentation.",
      "source_lang": "fr",
      "context_left": "",
      "context_right": "Aujourd'hui nous allons parler de...",
      "words": [
        { "text": "Bonjour", "start_ms": 1200, "end_ms": 1680, "confidence": 0.97 },
        { "text": "à", "start_ms": 1700, "end_ms": 1780, "confidence": 0.95 }
      ],
      "translations": {
        "en": {
          "text": "Hello everyone and welcome to this presentation.",
          "status": "completed",
          "timing_fit_status": "fit_ok",
          "mt_engine": "nllb-200-3.3b-ct2"
        }
      },
      "tts_outputs": {
        "en": {
          "status": "pending",
          "audio_uri": null,
          "duration_ms": null,
          "stretch_applied": false,
          "seed": 42
        }
      },
      "review_flags": []
    }
  ],

  "outputs": {
    "en": {
      "status": "pending",
      "audio_mix_uri": null,
      "video_uri": null,
      "srt_uri": null,
      "segments_done": 0,
      "segments_total": 87
    }
  },

  "metrics": {
    "processing_started_at": "2026-03-22T14:30:00Z",
    "processing_finished_at": null,
    "total_processing_time_s": null,
    "gpu_time_s": null
  }
}
```

---

## 9. Structure du monorepo

```
SILA/
│
├── README.md
├── MASTERPLAN.md                    ← Ce document
├── CHANGELOG.md
├── LICENSE
├── pyproject.toml                   ← Config projet Python (uv / poetry)
├── Makefile                         ← Commandes courantes
│
├── src/
│   ├── cli/                         ← Point d'entrée CLI (V1)
│   │   ├── __init__.py
│   │   └── main.py                  ← python -m src.cli.main --input video.mp4 --target-lang en
│   │
│   ├── pipeline/                    ← Orchestration du pipeline
│   │   ├── __init__.py
│   │   ├── runner.py                ← Exécution séquentielle V1
│   │   ├── dag.py                   ← Définition du DAG
│   │   └── stages.py                ← Enum des étapes + transitions
│   │
│   ├── core/                        ← Logique métier pure (pas de dépendance IA)
│   │   ├── __init__.py
│   │   ├── manifest.py              ← Lecture / écriture / validation du manifeste
│   │   ├── segment.py               ← Segmentation logique (règles métier)
│   │   ├── timing.py                ← Timing optimizer (cascade de durée)
│   │   ├── context.py               ← Construction du contexte global
│   │   └── models.py                ← Dataclasses / Pydantic models (contrats)
│   │
│   ├── engines/                     ← Moteurs IA (implémentations concrètes)
│   │   ├── __init__.py
│   │   ├── asr/
│   │   │   ├── interface.py          ← ASR_Interface (ABC)
│   │   │   ├── whisperx_engine.py    ← Implémentation WhisperX
│   │   │   ├── qwen3_asr_engine.py   ← Implémentation Qwen3-ASR [V2]
│   │   │   └── voxtral_engine.py     ← Implémentation Voxtral Mini Transcribe V2 [V2]
│   │   ├── mt/
│   │   │   ├── interface.py          ← MT_Interface (ABC)
│   │   │   ├── nllb_engine.py        ← Implémentation NLLB-200
│   │   │   └── madlad_engine.py      ← Implémentation MADLAD-400
│   │   ├── rewrite/
│   │   │   ├── interface.py          ← Rewriter_Interface (ABC)
│   │   │   └── mistral_engine.py     ← Implémentation Mistral Small 3.2
│   │   ├── tts/
│   │   │   ├── interface.py          ← TTS_Interface (ABC)
│   │   │   ├── cosyvoice_engine.py   ← Implémentation CosyVoice 3.0
│   │   │   └── qwen3_tts_engine.py   ← Implémentation Qwen3-TTS
│   │   ├── separation/
│   │   │   ├── interface.py          ← Separation_Interface (ABC)
│   │   │   └── demucs_engine.py      ← Implémentation Demucs v4
│   │   └── qc/
│   │       ├── interface.py          ← QC_Interface (ABC)
│   │       └── utmos_engine.py       ← Implémentation UTMOS
│   │
│   ├── media/                       ← Traitement média (FFmpeg, audio, mix)
│   │   ├── __init__.py
│   │   ├── ffmpeg.py                ← Wrappers FFmpeg (extract, remux, loudnorm)
│   │   ├── rubberband.py            ← Wrapper pyrubberband
│   │   ├── assembly.py              ← Placement segments sur timeline + crossfade
│   │   ├── mixer.py                 ← Mix voix + musique + SFX + ducking
│   │   └── voice_profile.py         ← Extraction embedding voix, sélection top-10
│   │
│   ├── api/                         ← API REST FastAPI [V2+]
│   │   ├── __init__.py
│   │   ├── app.py
│   │   ├── routes/
│   │   └── schemas/
│   │
│   └── workers/                     ← Workers Celery [V2+]
│       ├── __init__.py
│       ├── celery_app.py
│       ├── media_worker.py
│       ├── speech_worker.py
│       ├── translate_worker.py
│       ├── tts_worker.py
│       └── postprod_worker.py
│
├── configs/
│   ├── default.yaml                 ← Configuration par défaut
│   ├── models.yaml                  ← Registry des modèles (nom, version, chemin, VRAM)
│   ├── languages.yaml               ← Tiers de langues supportées (tier1, tier2)
│   └── glossaries/                  ← Glossaires par projet / domaine
│       └── example_glossary.json
│
├── data/                            ← Données de projet (gitignored)
│   └── projects/
│       └── {project_id}/
│           ├── source/
│           │   └── input.mp4
│           ├── extracted/
│           │   └── audio.wav
│           ├── stems/               ← [V2+]
│           │   ├── voice.wav
│           │   ├── music.wav
│           │   └── sfx.wav
│           ├── asr/
│           │   └── transcript.json
│           ├── voice_refs/
│           │   ├── spk_0_ref.wav
│           │   └── spk_0_embedding.pt
│           ├── tts/
│           │   └── {lang}/
│           │       ├── seg_0001.wav
│           │       └── seg_0001_adj.wav
│           ├── mix/
│           │   └── {lang}/
│           │       └── mix_final.wav
│           ├── exports/
│           │   ├── output_{lang}.mp4
│           │   └── {lang}.srt
│           ├── manifest.json
│           └── qc_report.json
│
├── models/                          ← Poids des modèles (gitignored)
│   ├── whisperx/
│   ├── nllb-200-3.3b-ct2/
│   ├── cosyvoice3-0.5b/
│   ├── qwen3-tts-1.7b/             ← [benchmark V2]
│   ├── mistral-small-3.2-24b/      ← [V2]
│   ├── demucs-htdemucs_ft/         ← [V2]
│   └── utmos/
│
├── datasets/
│   ├── golden_set/                  ← Vidéos de référence pour benchmarks
│   │   ├── 01_podcast_fr_10min.mp4
│   │   ├── 02_presentation_en_5min.mp4
│   │   └── expected_outputs/
│   └── eval_reports/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   └── regression/
│       ├── audio_regression/        ← Comparaison audio output vs référence
│       └── timing_regression/       ← Vérification drift temporel
│
├── scripts/
│   ├── download_models.sh           ← Téléchargement de tous les modèles
│   ├── benchmark_tts.py             ← Benchmark CosyVoice vs Qwen3-TTS
│   ├── benchmark_mt.py              ← Benchmark NLLB vs MADLAD
│   └── validate_manifest.py         ← Validation schéma manifeste
│
├── docker/
│   ├── Dockerfile.base              ← Image de base (Python, FFmpeg, libs système)
│   ├── Dockerfile.gpu               ← Image GPU (CUDA, PyTorch, modèles)
│   ├── Dockerfile.api               ← Image API FastAPI [V2+]
│   ├── Dockerfile.worker            ← Image worker Celery [V2+]
│   ├── docker-compose.v1.yml        ← V1 : container unique, CLI
│   ├── docker-compose.v2.yml        ← V2 : API + Redis + PostgreSQL + workers
│   └── docker-compose.v3.yml        ← V3 : + Temporal + MinIO + Prometheus
│
├── infra/                           ← [V3]
│   ├── temporal/
│   │   └── workflows/
│   ├── prometheus/
│   │   └── prometheus.yml
│   └── grafana/
│       └── dashboards/
│
└── docs/
    ├── architecture/
    │   ├── adr/                      ← Architecture Decision Records
    │   │   ├── 001-cosyvoice-over-xtts.md
    │   │   ├── 002-nllb-licence-risk.md
    │   │   └── 003-celery-over-temporal-v1.md
    │   └── diagrams/
    ├── api/                          ← Spécifications API [V2+]
    ├── runbooks/                     ← Procédures d'exploitation
    └── guides/
        ├── getting-started.md
        ├── adding-a-language.md
        └── adding-a-tts-engine.md
```

---

## 10. Architecture matérielle

### 10.1 Profils GPU

| Profil | Hardware | Usage | Capacité estimée |
|---|---|---|---|
| **Minimum (V1)** | 1× RTX 4090 (24 Go VRAM) | WhisperX + CosyVoice séquentiel | ~10 min vidéo / heure traitement / langue |
| **Recommandé (V2)** | 2× RTX 4090 ou 1× A6000 (48 Go) | ASR sur GPU0, TTS batch sur GPU1 | ~30 min vidéo / heure / langue |
| **Production (V3)** | 2-4× RTX 4090 + allocation dynamique | Parallélisme complet | ~1h vidéo / heure / 3 langues |

### 10.2 Budget VRAM par étape (séquentiel sur 1 GPU)

| Étape | Modèle | VRAM | Durée estimée (1h vidéo) |
|---|---|---|---|
| Demucs v4 | htdemucs_ft | ~4 Go | ~10 min |
| WhisperX | large-v3 + pyannote | ~8 Go | ~15 min |
| NLLB-200 | 3.3B int8 CPU | 0 GPU / ~6 Go RAM | ~5 min |
| CosyVoice 3.0 | 0.5B | ~4 Go | ~60-120 min (goulot) |
| Mistral Small 3.2 | 24B Q4_K_M | ~15 Go | ~10 min (réécriture seule) |
| UTMOS | — | ~1 Go | ~5 min |

**VRAM peak en séquentiel** : ~15 Go (Mistral seul) ou ~8 Go (WhisperX seul). Jamais les deux simultanément en V1.

**Cohabitation critique en V2** : CosyVoice (4 Go) + NLLB CPU = ok. Mais CosyVoice + Mistral Small 3.2 = 19 Go → ça passe sur 24 Go, tight. Le fallback Ministral 3 8B (~5 Go) existe pour ça.

### 10.3 Stockage estimé

| Composant | Taille estimée (1h vidéo, 3 langues) |
|---|---|
| Vidéo source | ~5 Go |
| Audio extrait (48 kHz WAV mono) | ~600 Mo |
| Stems Demucs (3 pistes) | ~1.8 Go |
| Segments TTS bruts + ajustés | ~3-6 Go |
| Mix finaux | ~1.2 Go |
| Exports MP4 finaux | ~15 Go |
| Manifeste + rapports | ~5 Mo |
| **Total** | **~27 Go** |
| **Après purge intermédiaires** | **~21 Go** |

---

## 11. Infrastructure et déploiement

### 11.1 Docker Compose V1 (CLI mono-container)

```yaml
# docker-compose.v1.yml
version: '3.8'

services:
  pipeline:
    build:
      context: .
      dockerfile: docker/Dockerfile.gpu
    volumes:
      - ./data:/app/data
      - ./models:/app/models
      - ./configs:/app/configs
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    environment:
      - CUDA_VISIBLE_DEVICES=0
      - PIPELINE_CONFIG=/app/configs/default.yaml
    command: >
      python -m src.cli.main
        --input /app/data/projects/demo/source/input.mp4
        --target-lang en
        --config /app/configs/default.yaml
```

### 11.2 Docker Compose V2 (API + Workers)

```yaml
# docker-compose.v2.yml
version: '3.8'

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: pipeline
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: dubbing_db
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    restart: always

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    restart: always

  api:
    build:
      context: .
      dockerfile: docker/Dockerfile.api
    ports:
      - "8000:8000"
    depends_on: [postgres, redis]
    volumes:
      - ./data:/app/data
      - ./configs:/app/configs
    environment:
      - DATABASE_URL=postgresql://pipeline:${DB_PASSWORD}@postgres:5432/dubbing_db
      - REDIS_URL=redis://redis:6379/0

  worker-media:
    build:
      context: .
      dockerfile: docker/Dockerfile.worker
    depends_on: [redis]
    volumes:
      - ./data:/app/data
      - ./models:/app/models
    command: celery -A src.workers.celery_app worker -Q media -c 2

  worker-speech:
    build:
      context: .
      dockerfile: docker/Dockerfile.gpu
    depends_on: [redis]
    volumes:
      - ./data:/app/data
      - ./models:/app/models
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    command: celery -A src.workers.celery_app worker -Q speech -c 1
    environment:
      - CUDA_VISIBLE_DEVICES=0

  worker-translate:
    build:
      context: .
      dockerfile: docker/Dockerfile.worker
    depends_on: [redis]
    volumes:
      - ./data:/app/data
      - ./models:/app/models
    command: celery -A src.workers.celery_app worker -Q translate -c 4

  worker-tts:
    build:
      context: .
      dockerfile: docker/Dockerfile.gpu
    depends_on: [redis]
    volumes:
      - ./data:/app/data
      - ./models:/app/models
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    command: celery -A src.workers.celery_app worker -Q tts -c 1
    environment:
      - CUDA_VISIBLE_DEVICES=0

  worker-postprod:
    build:
      context: .
      dockerfile: docker/Dockerfile.worker
    depends_on: [redis]
    volumes:
      - ./data:/app/data
    command: celery -A src.workers.celery_app worker -Q postprod -c 2

  flower:
    image: mher/flower:2.0
    depends_on: [redis]
    ports:
      - "5555:5555"
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0

volumes:
  pgdata:
```

### 11.3 Conventions de nommage des artefacts

```
data/projects/{project_id}/source/input.mp4
data/projects/{project_id}/extracted/audio_48k.wav
data/projects/{project_id}/stems/voice.wav
data/projects/{project_id}/stems/music.wav
data/projects/{project_id}/stems/sfx.wav
data/projects/{project_id}/asr/transcript.json
data/projects/{project_id}/voice_refs/spk_0_ref.wav
data/projects/{project_id}/voice_refs/spk_0_embedding.pt
data/projects/{project_id}/tts/{lang}/seg_{index:04d}.wav
data/projects/{project_id}/tts/{lang}/seg_{index:04d}_adj.wav
data/projects/{project_id}/mix/{lang}/mix_final.wav
data/projects/{project_id}/exports/output_{lang}.mp4
data/projects/{project_id}/exports/{lang}.srt
data/projects/{project_id}/manifest.json
data/projects/{project_id}/qc_report.json
```

**Règles** : nommage déterministe, index 4 chiffres paddés, suffixe `_adj` pour les fichiers post-stretch, pas de modification in-place.

---

## 12. Stratégie de gestion d'erreurs et reprise

### 12.1 Clé d'idempotence

```
idempotency_key = sha256(
  project_id +
  stage_name +
  target_lang +
  segment_id +
  model_name + model_version +
  params_hash +
  input_artifact_hash
)
```

Avant chaque exécution, vérifier si un artefact avec cette clé existe déjà et est valide → skip.

### 12.2 Politique de retry

| Cas | Action |
|---|---|
| OOM GPU, timeout réseau, erreur stockage | Retry automatique 3× avec backoff exponentiel (2s, 8s, 32s) |
| Worker crash | Celery re-dispatch automatique |
| Langue non supportée par le TTS | Fallback vers moteur alternatif ou hard-fail avec flag |
| Voice profile manquant | Flag `review_required` + fallback voix générique |
| Timing impossible (stretch > 1.25×) | Flag `review_required`, garder le segment stretché à 1.25× |
| Segment pathologique (overlap extrême, bruit) | Quarantaine + flag `manual_review` |
| 3 échecs consécutifs | Dead letter → statut `failed` dans le manifeste |

### 12.3 Granularité de relance

| Relance possible | Commande V1 |
|---|---|
| Un segment spécifique pour une langue | `--retry segment seg_0042 --lang en` |
| Tous les segments `failed` d'une langue | `--retry failed --lang en` |
| Toute une langue depuis la traduction | `--retry from-stage translate --lang en` |
| L'assembly seul | `--retry stage assembly --lang en` |
| L'export seul | `--retry stage export --lang en` |

### 12.4 Validation avant export

Avant l'export final, vérifier que 100% des segments ont un statut `completed` pour la langue. Si non :
- Si < 5% en `failed` → proposer un export partiel (segments failed = silence).
- Si > 5% en `failed` → bloquer l'export, signaler à l'opérateur.

---

## 13. KPI et quality gates

### 13.1 KPI de performance

| Métrique | Cible V1 | Cible V2 | Mesure |
|---|---|---|---|
| Ratio temps traitement / durée vidéo | < 10× | < 5× | Timer global |
| Taux d'échec segment | < 5% | < 1% | Manifeste |
| Cache hit rate | — | > 30% | Manifeste |
| GPU-minutes par heure de vidéo par langue | Mesurer baseline | < baseline × 0.7 | Logs |

### 13.2 KPI de qualité audio

| Métrique | Cible V1 | Cible V2 | Mesure |
|---|---|---|---|
| MOS estimé (UTMOS) | > 3.5/5 | > 3.8/5 | UTMOS par segment |
| Segments dans le slot ±15% | > 85% | > 90% | Manifeste |
| Drift temporel moyen / segment | < 200 ms | < 100 ms | Manifeste |
| Déviation durée totale vs source | < 2% | < 1% | Calcul post-assembly |
| Segments stretchés > 1.15× | < 15% | < 8% | Manifeste |
| Clipping rate | 0% | 0% | FFmpeg stats |
| True peak | < -1 dBTP | < -1 dBTP | FFmpeg stats |
| Loudness final | -16 ± 1 LUFS | -16 ± 0.5 LUFS | FFmpeg loudnorm |

### 13.3 KPI de qualité contenu

| Métrique | Cible V1 | Cible V2 | Mesure |
|---|---|---|---|
| VRAM peak | < 24 Go | < 24 Go | nvidia-smi |
| Taux de segments `review_required` | < 10% | < 5% | Manifeste |
| Cohérence voix par speaker (subjectif) | Acceptable | Bon | Review humaine |

### 13.4 Quality gates

| Gate | Condition de passage | Conséquence si échec |
|---|---|---|
| Post-ASR | WER sur golden set < 10% | Ne pas poursuivre, investiguer |
| Post-traduction | 0 segment sans traduction | Retry ou review |
| Post-TTS | UTMOS moyen > 3.5 [V2+] | Retry avec paramètres ajustés |
| Pré-export | 100% segments completed | Bloquer export si > 5% failed |
| Post-export | Loudness -16 ± 1 LUFS | Re-normalisation |

---

## 14. Roadmap V1 / V2 / V3

### 14.1 V1 — "Proof of Value" (6-8 semaines)

**Objectif** : produire un doublage écoutable de bout en bout sur une vidéo courte.

| Inclus | Exclu |
|---|---|
| CLI Python | API REST |
| Vidéo ≤ 10 min | Vidéos > 10 min |
| 1 locuteur unique | Multi-locuteurs |
| 1 langue cible | Multi-langues parallèles |
| CosyVoice 3.0 | Qwen3-TTS |
| WhisperX (monolithique) | Décomposition ASR |
| Manifeste JSON fichier | PostgreSQL |
| Script séquentiel | Celery / Redis |
| Nommage déterministe | S3 / MinIO |
| Reprise par étape | Reprise par segment individuel |
| FFmpeg loudnorm global | pyloudnorm segment par segment |
| Pas de Demucs | — |
| Pas de diarisation | — |
| Pas de réécriture LLM | — |
| Pas de lip-sync | — |
| Pas de parallélisme | — |

**Livrables V1** : CLI fonctionnel, manifeste JSON complet, MP4 exporté, SRT synchronisé, rapport QC basique.

### 14.2 V2 — "Production interne" (3-4 mois après V1)

| Ajout | Détail |
|---|---|
| Multi-locuteurs | Diarisation pyannote, mapping speaker → voice |
| Multi-langues | Fan-out parallèle après segmentation |
| Demucs v4 | Séparation vocale, mix avec stems |
| Décomposition WhisperX | Whisper + alignement + diarisation séparés |
| Évaluation ASR V2 | Benchmark WhisperX vs Qwen3-ASR vs Voxtral Mini Transcribe V2 sur golden set |
| Réécriture contrainte | Mistral Small 3.2 24B (Unsloth Q4_K_M) |
| Celery + Redis | Dispatch et parallélisme des tâches |
| API REST FastAPI | Upload, suivi, téléchargement |
| PostgreSQL JSONB | Index de consultation, suivi progression |
| UTMOS | Quality gate automatique |
| Benchmark TTS | CosyVoice vs Qwen3-TTS sur golden set |
| Vidéos longues (1h+) | Chunking technique + réconciliation |
| Glossaire projet | Injection dans traduction et réécriture |
| pyloudnorm | Normalisation segment par segment |

### 14.3 V3 — "Produit" (3-4 mois après V2)

| Ajout | Détail |
|---|---|
| UI review | Interface web pour review segmentaire (traduction, timing, audio) |
| Lip-sync conditionnel | Uniquement segments face-caméra avec désync visible |
| Détection émotions | Adaptation prosodie TTS selon émotion détectée |
| Export multi-piste | Pistes séparées (voix, musique, SFX) par langue |
| Temporal | Orchestration durable, workflows complexes |
| S3 / MinIO | Stockage objet scalable |
| Monitoring Prometheus + Grafana | Dashboards temps réel |
| Auto-routing TTS | Choix du meilleur moteur par langue/speaker/contenu |

---

## 15. Veille technologique et repos à surveiller

### 15.1 Repos critiques (suivi hebdomadaire)

| Repo | Raison | Action si changement |
|---|---|---|
| [FunAudioLLM/CosyVoice](https://github.com/FunAudioLLM/CosyVoice) | TTS principal. Releases, bugs, nouvelles langues. | Évaluer chaque release majeure |
| [QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) | TTS alternatif. Benchmark croisé. | Benchmark chaque release |
| [m-bain/whisperX](https://github.com/m-bain/whisperX) | ASR principal. Maintenance status. | Si stale > 6 mois, évaluer BetterWhisperX |
| [lihqi/BetterWhisperX](https://github.com/lihqi/BetterWhisperX) | Fork maintenu de WhisperX. | Candidat remplacement si WhisperX stagne |
| [facebookresearch/demucs](https://github.com/facebookresearch/demucs) | Séparation vocale. Repo archivé. | Surveiller les forks actifs |
| [OpenNMT/CTranslate2](https://github.com/OpenNMT/CTranslate2) | Runtime d'inférence MT. | Compatibilité nouveaux modèles |
| [unsloth/unsloth](https://github.com/unslothai/unsloth) | Quantification GGUF optimisée. | Nouvelles quants Mistral / LLM |
| [mistralai](https://github.com/mistralai) | Écosystème Mistral complet : LLM (Small 3.x, Small 4, Ministral 3), audio (Voxtral), code (Devstral). | Nouvelles versions pour réécriture (LLM) et ASR (Voxtral) |
| [mistralai/Voxtral-Mini-4B-Realtime-2602](https://github.com/mistralai) | ASR streaming + batch, diarisation native, 13 langues, context biasing, Apache 2.0. | Candidat remplacement WhisperX en V2. Benchmark sur golden set SILA vs WhisperX vs Qwen3-ASR |

### 15.2 Modèles à surveiller (veille mensuelle)

| Domaine | Modèle à surveiller | Pourquoi |
|---|---|---|
| ASR | **Voxtral Mini Transcribe V2** (Mistral) | Transcription + diarisation + timestamps + context biasing dans un seul modèle 4B. Apache 2.0. ~16 Go VRAM. Surpasse Whisper large-v3 sur FLEURS (~4% WER). Février 2026. Candidat prioritaire V2. |
| ASR | **Voxtral Realtime 4B** (Mistral) | Streaming ASR, latence configurable 200ms-2.4s. Apache 2.0 open-weights. Même architecture que Transcribe V2 mais optimisé temps réel. Pertinent si SILA évolue vers du near-realtime. |
| ASR | **Qwen3-ASR** (Alibaba) | Nouveau SOTA ASR open-source début 2026. Candidat remplacement Whisper en V2. |
| ASR | **Canary Qwen 2.5B** (NVIDIA) | Top HuggingFace Open ASR leaderboard. Pas de diarisation intégrée. |
| TTS | **GLM-TTS** (Zhipu) | Reinforcement learning, bonne qualité zh/en. |
| TTS | **Orpheus TTS** | Si le multilingue sort de "research preview". |
| MT | **MADLAD-400** | Alternative Apache 2.0 à NLLB. |
| MT | **Open-NLLB** | Effort communautaire pour des poids NLLB sous licence ouverte. |
| LLM | **Mistral Small 4** (119B MoE, 6B actifs) | Unifie reasoning + vision + coding. Apache 2.0. Trop lourd pour V1-V2 (242 Go), mais pertinent V3 si infra multi-GPU. Mars 2026. |
| LLM | **Qwen3.5** | Modèles Alibaba dernière génération. |
| Séparation | **Bandit v2** | Successeur potentiel de Demucs. |
| Lip-sync | **MuseTalk** / **Hallo2** | Alternatives open-source à Wav2Lip pour V3. |

### 15.3 Benchmarks de référence

| Domaine | Benchmark | Usage |
|---|---|---|
| ASR | LibriSpeech, CommonVoice, FLEURS | WER par langue |
| MT | FLORES-200, WMT | BLEU, COMET par paire |
| TTS | SEED-TTS-Eval, CV3-Eval | CER, speaker similarity, MOS |
| Audio qualité | UTMOS, DNSMOS, PESQ | MOS estimé |
| Diarisation | AMI, CALLHOME, DIHARD | DER |

---

## 16. Risques identifiés et mitigations

| # | Risque | Probabilité | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **Licence NLLB-200** bloque usage commercial | Haute si commercial | Bloquant | Basculer sur MADLAD-400 (Apache 2.0) |
| R2 | **TTS goulot d'étranglement** — 60-120 min pour 1h de vidéo | Certaine | Fort (scalabilité) | Multi-GPU, TensorRT-LLM, batch segments |
| R3 | **Voice cloning inégal** entre langues | Haute | Moyen (qualité perçue) | Définir tiers de langues. Tier 1 = bien testé, tier 2 = best-effort |
| R4 | **Diarisation fragile** > 2-3 locuteurs | Haute | Fort (contamine tout l'aval) | Mode correction manuelle, flag `review_required` |
| R5 | **WhisperX non maintenu** (repo stale) | Moyenne | Moyen | BetterWhisperX comme fallback, décomposition V2 |
| R6 | **Demucs archivé** par Meta | Avéré | Faible (poids figés fonctionnent) | Surveiller forks, Bandit v2 comme successeur |
| R7 | **Timing cassé** malgré la cascade | Moyenne | Fort (rendu inutilisable) | Quality gate post-TTS, review si > seuil |
| R8 | **Mix final amateur** sans stems | Haute en V1 | Moyen (V1 cible voix-only) | V1 cible podcasts/cours. Demucs dès V2. |
| R9 | **Over-engineering V1** tue le projet | Moyenne | Fatal | Respecter le scope V1. CLI + manifeste + 1 langue = SUFFISANT. |
| R10 | **Cohérence voix** sur 1h de contenu | Haute | Moyen | Embedding moyen (top-10), seed fixe, monitoring UTMOS |
| R11 | **Évolution rapide** des modèles TTS | Certaine | Positif si géré | Principe P12 (interchangeabilité). Interfaces abstraites. |
| R12 | **Cohabitation VRAM** LLM + TTS sur 1 GPU | Moyenne (V2) | Moyen | Ministral 3 8B comme fallback léger, scheduling séquentiel |

---

## 17. Glossaire

| Terme | Définition |
|---|---|
| **Timing contract** | Engagement de durée maximale d'un segment, fixé dès la segmentation |
| **Timing budget** | Durée en ms allouée à un segment pour le TTS + stretch |
| **Cascade de durée** | Stratégie multi-étapes pour respecter le timing (traduction courte → TTS rapide → stretch léger) |
| **Tronc commun** | Étapes exécutées une seule fois quelle que soit le nombre de langues cibles |
| **Segment logique** | Unité de traduction/TTS définie par des règles métier (3-10s, mono-speaker) |
| **Chunk technique** | Découpage physique pour le calcul distribué (30-120s avec overlap) |
| **Stem** | Piste audio séparée (voix, musique, SFX) produite par Demucs |
| **Golden set** | Ensemble de vidéos de référence avec sorties attendues pour les benchmarks |
| **Quality gate** | Condition à remplir avant de passer à l'étape suivante |
| **Dead letter** | Segment ayant échoué 3× → quarantaine pour intervention manuelle |
| **Fan-out** | Point du DAG où le pipeline se ramifie en N branches (1 par langue cible) |
| **Idempotence** | Propriété d'une opération qui, exécutée plusieurs fois, produit le même résultat |
| **Ducking** | Réduction automatique du volume de la musique sous la voix |
| **LUFS** | Loudness Units relative to Full Scale — unité de mesure du loudness perçu |
| **CER** | Character Error Rate — taux d'erreur au caractère (TTS) |
| **WER** | Word Error Rate — taux d'erreur au mot (ASR) |
| **DER** | Diarization Error Rate — taux d'erreur de la diarisation |
| **MOS** | Mean Opinion Score — score perceptif de qualité audio (1-5) |
| **UTMOS** | Estimateur automatique de MOS basé sur un modèle pré-entraîné |

---

## 18. Historique des décisions (ADR)

### ADR-001 : CosyVoice 3.0 plutôt que XTTS v2
- **Date** : 2026-03-22
- **Contexte** : Besoin d'un TTS multilingue avec voice cloning self-hosted.
- **Décision** : CosyVoice 3.0 (Apache 2.0) plutôt que XTTS v2 (CPML non-commercial, Coqui fermé).
- **Conséquence** : 9 langues couvertes nativement. Pronunciation inpainting disponible.

### ADR-002 : NLLB-200 avec risque licence identifié
- **Date** : 2026-03-22
- **Contexte** : NLLB-200 3.3B est le meilleur modèle MT self-hosted, mais licence CC-BY-NC 4.0.
- **Décision** : Garder NLLB-200 pour V1 (usage interne). Préparer MADLAD-400 comme alternative.
- **Conséquence** : Si usage commercial, swap vers MADLAD-400 nécessaire.

### ADR-003 : Celery + Redis plutôt que Temporal en V1-V2
- **Date** : 2026-03-22
- **Contexte** : Temporal est plus puissant mais plus complexe à opérer.
- **Décision** : V1 = script séquentiel. V2 = Celery + Redis. V3 = Temporal.
- **Conséquence** : Complexité opérationnelle minimale au démarrage.

### ADR-004 : Mistral Small 3.2 24B pour la réécriture contrainte
- **Date** : 2026-03-22
- **Contexte** : Besoin d'un LLM multilingue local pour réécrire les traductions trop longues.
- **Décision** : Mistral Small 3.2 24B (Unsloth Dynamic 2.0 Q4_K_M) ~15 Go VRAM. Fallback Ministral 3 8B ~5 Go.
- **Justification** : Apache 2.0, 40+ langues, tient sur 1× RTX 4090. Mistral Small 4 (119B) trop lourd pour <24 Go VRAM.

### ADR-005 : Pas de Demucs en V1
- **Date** : 2026-03-22
- **Contexte** : Demucs améliore la qualité mais double la complexité.
- **Décision** : V1 cible des contenus voix-only (podcasts, cours). Demucs ajouté en V2.
- **Conséquence** : Mix V1 plus simple. Acceptable pour le cas d'usage V1.

---

*Fin du masterplan. Ce document est versionné et fait autorité sur toutes les décisions du projet.*
