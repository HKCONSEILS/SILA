"""Extraction embedding voix et selection top-10 segments.

Voir MASTERPLAN.md §2.1 P6 — profil voix global, embedding moyen sur top-10 segments.
V1 : simplifie — utilise un extrait de reference unique du locuteur.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# V1 : profil voix simplifie. Le module complet avec embedding moyen sera en V2.
