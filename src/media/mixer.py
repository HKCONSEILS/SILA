"""Mix voix + musique + SFX + ducking.

Voir MASTERPLAN.md §6.1 Phase 9.3-9.4.
V1 : pas de stems (ADR-005), donc ce module est minimal.
V2+ : mix avec Demucs stems et ducking automatique.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# V1 : pas de mix multi-pistes. Le module sera etoffe en V2 avec Demucs.
