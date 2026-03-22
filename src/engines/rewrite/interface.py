"""Interface abstraite pour la reecriture contrainte.

Voir MASTERPLAN.md §5.3 — Rewriter_Interface.
V1 : pas de reecriture LLM (§14.1). Implementee en V2.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RewriteResult:
    """Resultat de reecriture contrainte. Voir MASTERPLAN.md §5.3."""

    text: str
    char_count: int = 0
    fit_status: str = "fit_ok"


class RewriterInterface(ABC):
    """Interface abstraite pour la reecriture. Voir MASTERPLAN.md §5.3."""

    @abstractmethod
    def rewrite(
        self,
        text: str,
        target_lang: str,
        max_chars: int,
        timing_budget_ms: int,
        context: str = "",
    ) -> RewriteResult:
        ...
