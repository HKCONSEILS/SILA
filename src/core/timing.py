"""Timing optimizer — cascade de duree qualite-first.

Voir MASTERPLAN.md v1.3.0 §2.1 P2, P15, P16.
Philosophie : adapter le texte au budget, pas forcer le TTS a parler vite.
Speed max 1.2x, stretch max 1.10x, reecriture LLM comme composant central.
"""

from __future__ import annotations

import logging

from src.core.models import TimingFitStatus

logger = logging.getLogger(__name__)

# Seuils qualite-first
TIMING_FIT_TOLERANCE = 0.15  # ±15% pour QC pass
MAX_STRETCH_RATIO = 1.10     # stretch max (qualite-first, etait 1.50)
MAX_SPEED_RATIO = 1.20       # speed TTS max (qualite-first, etait 2.0)
MIN_SLOWDOWN_RATIO = 0.85    # ralentissement max pour combler silence

# Debits naturels TTS par langue (chars/s a speed=1.0)
NATURAL_SPEECH_RATES = {
    "en": 10, "fr": 12, "es": 11, "de": 10, "pt": 11,
    "it": 11, "nl": 10, "ar": 8, "hi": 9,
}


def calc_max_chars(budget_ms: int, lang: str, margin: float = 0.90) -> int:
    """Calcule le nombre max de caracteres pour un budget donne.

    Formule : max_chars = (budget_ms / 1000) * debit_naturel * margin
    La marge de 0.90 laisse 10% pour le stretch fin.

    Args:
        budget_ms: Budget temporel en ms.
        lang: Code ISO 639-1 de la langue cible.
        margin: Marge de securite (0.90 = 90% du budget).

    Returns:
        Nombre max de caracteres.
    """
    rate = NATURAL_SPEECH_RATES.get(lang, 10)
    return int((budget_ms / 1000) * rate * margin)


def classify_timing_fit_text(
    text: str,
    budget_ms: int,
    target_lang: str,
) -> TimingFitStatus:
    """Classifie l'adequation temporelle basee sur le texte traduit.

    Utilise max_chars pour decider si le texte tient dans le budget.

    Args:
        text: Texte traduit.
        budget_ms: Budget temporel en ms.
        target_lang: Langue cible.

    Returns:
        FIT_OK, REWRITE_NEEDED, ou REVIEW_REQUIRED.
    """
    max_chars = calc_max_chars(budget_ms, target_lang)

    if max_chars <= 0:
        return TimingFitStatus.REVIEW_REQUIRED

    char_count = len(text)

    if char_count <= max_chars * 1.15:
        return TimingFitStatus.FIT_OK
    elif char_count <= max_chars * 1.30:
        return TimingFitStatus.REWRITE_NEEDED
    else:
        return TimingFitStatus.REVIEW_REQUIRED


def classify_timing_fit(
    actual_duration_ms: int,
    budget_ms: int,
) -> TimingFitStatus:
    """Classifie l'adequation temporelle d'un segment (basee sur duree).

    Args:
        actual_duration_ms: Duree reelle (estimee ou mesuree).
        budget_ms: Budget temporel alloue.

    Returns:
        fit_ok si dans la tolerance, rewrite_needed ou review_required sinon.
    """
    if budget_ms <= 0:
        return TimingFitStatus.REVIEW_REQUIRED

    ratio = actual_duration_ms / budget_ms

    if ratio <= 1.0 + TIMING_FIT_TOLERANCE:
        return TimingFitStatus.FIT_OK

    if ratio <= 1.30:
        return TimingFitStatus.REWRITE_NEEDED

    return TimingFitStatus.REVIEW_REQUIRED


def compute_stretch_ratio(
    actual_duration_ms: int,
    budget_ms: int,
) -> float:
    """Calcule le ratio de stretch necessaire.

    Returns:
        Ratio (> 1.0 = accelerer, < 1.0 = ralentir). 1.0 si pas de stretch.
    """
    if budget_ms <= 0 or actual_duration_ms <= budget_ms:
        return 1.0
    return actual_duration_ms / budget_ms
