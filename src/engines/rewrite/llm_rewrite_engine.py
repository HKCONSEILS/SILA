"""LLM rewrite engine — Phase 7 (reecriture contrainte).

Voir MASTERPLAN.md §6.1 Phase 7 — reecriture contrainte LLM.
Utilise Qwen3.5-27B sur LXC 225 via API OpenAI-compatible (completions).
"""

from __future__ import annotations

import logging
import os

import httpx

from src.engines.rewrite.interface import RewriterInterface, RewriteResult

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = (
    "Shorten this {lang} text to under {max_chars} characters. "
    "Keep the meaning and register. Output ONLY the shortened text, nothing else.\n\n"
    "Original: {text}\n\n"
    "Shortened:"
)

LANG_NAMES = {
    "en": "English", "fr": "French", "es": "Spanish",
    "de": "German", "it": "Italian", "pt": "Portuguese",
}


class LLMRewriteEngine(RewriterInterface):
    """Reecriture contrainte via LLM local (API OpenAI completions)."""

    def __init__(
        self,
        api_base: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ):
        self.api_base = api_base or os.environ.get(
            "SILA_LLM_API_BASE", "http://192.168.1.225:8080"
        )
        self.model = model or os.environ.get(
            "SILA_LLM_MODEL", "Qwen3.5-27B-Q8_0.gguf"
        )
        self.timeout = timeout
        self._client = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.api_base,
                timeout=self.timeout,
            )
        return self._client

    def rewrite(
        self,
        text: str,
        target_lang: str,
        max_chars: int,
        timing_budget_ms: int,
        context: str = "",
    ) -> RewriteResult:
        """Reecrit un texte pour le raccourcir sous max_chars."""
        lang_name = LANG_NAMES.get(target_lang, target_lang)
        prompt = PROMPT_TEMPLATE.format(
            lang=lang_name,
            max_chars=max_chars,
            text=text,
        )

        try:
            client = self._get_client()
            response = client.post(
                "/v1/completions",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "temperature": 0.3,
                    "max_tokens": max(80, max_chars),
                    "stop": ["\n\n", "Original:"],
                },
            )
            response.raise_for_status()
            data = response.json()

            rewritten = data["choices"][0]["text"].strip()

            # Clean up quotes
            if rewritten.startswith(chr(34)) and rewritten.endswith(chr(34)):
                rewritten = rewritten[1:-1].strip()

            char_count = len(rewritten)

            if char_count <= max_chars:
                fit_status = "fit_ok"
            elif char_count <= max_chars * 1.15:
                fit_status = "rewrite_needed"
            else:
                fit_status = "review_required"

            logger.info(
                "Rewrite %s: %d -> %d chars (max %d) — %s",
                target_lang, len(text), char_count, max_chars, fit_status,
            )

            return RewriteResult(
                text=rewritten,
                char_count=char_count,
                fit_status=fit_status,
            )

        except Exception as e:
            logger.warning("LLM rewrite failed: %s — keeping original", e)
            return RewriteResult(
                text=text,
                char_count=len(text),
                fit_status="review_required",
            )

    def close(self):
        if self._client:
            self._client.close()
            self._client = None
