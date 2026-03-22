"""Timing optimizer — cascade de duree.

Voir MASTERPLAN.md §2.1 P2 — cascade de duree :
  Segmenter -> budget -> traduire avec contrainte -> reecrire si necessaire
  -> ajuster prosodie TTS -> time-stretch <= 1.25x en dernier recours.

V1 : pas de reecriture LLM, donc la cascade est simplifiee.
"""

from __future__ import annotations

import logging

from src.core.models import TimingFitStatus

logger = logging.getLogger(__name__)

# Seuils de timing. Voir MASTERPLAN.md §13.2.
TIMING_FIT_TOLERANCE = 0.15  # ±15%
MAX_STRETCH_RATIO = 1.25


def classify_timing_fit(
    actual_duration_ms: int,
    budget_ms: int,
) -> TimingFitStatus:
    """Classifie l'adequation temporelle d'un segment.

    Voir MASTERPLAN.md §6.1 Phase 6.3 — classer timing fit.

    Args:
        actual_duration_ms: Duree reelle (estimee ou mesuree).
        budget_ms: Budget temporel alloue (timing_budget_ms).

    Returns:
        fit_ok si dans la tolerance, rewrite_needed ou review_required sinon.
    """
    if budget_ms <= 0:
        return TimingFitStatus.REVIEW_REQUIRED

    ratio = actual_duration_ms / budget_ms

    if ratio <= 1.0 + TIMING_FIT_TOLERANCE:
        return TimingFitStatus.FIT_OK

    stretch_needed = ratio
    if stretch_needed <= MAX_STRETCH_RATIO:
        return TimingFitStatus.REWRITE_NEEDED

    return TimingFitStatus.REVIEW_REQUIRED


def compute_stretch_ratio(
    actual_duration_ms: int,
    budget_ms: int,
) -> float:
    """Calcule le ratio de stretch necessaire.

    Voir MASTERPLAN.md §6.1 Phase 8.3 — time-stretch <= 1.25x.

    Returns:
        Ratio (> 1.0 si besoin d'accelerer). 1.0 si pas de stretch.
    """
    if budget_ms <= 0 or actual_duration_ms <= budget_ms:
        return 1.0
    return actual_duration_ms / budget_ms
