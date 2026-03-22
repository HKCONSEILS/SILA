"""Interface abstraite pour les moteurs de traduction.

Voir MASTERPLAN.md §5.3 — MT_Interface.
Principe P12 : separer interface de tache et moteur concret.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TranslationResult:
    """Resultat de traduction. Voir MASTERPLAN.md §5.3."""

    text: str
    estimated_chars: int = 0
    confidence: float = 0.0


class MTInterface(ABC):
    """Interface abstraite pour la traduction automatique. Voir MASTERPLAN.md §5.3."""

    @abstractmethod
    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: list[str] | None = None,
        glossary: dict[str, str] | None = None,
    ) -> TranslationResult:
        """Traduit un segment de texte.

        Args:
            text: Texte source a traduire.
            source_lang: Code ISO 639-1 source.
            target_lang: Code ISO 639-1 cible.
            context: Segments de contexte (fenetre glissante).
            glossary: Glossaire projet (terme source -> terme cible).

        Returns:
            TranslationResult avec texte traduit et estimation.
        """
        ...
