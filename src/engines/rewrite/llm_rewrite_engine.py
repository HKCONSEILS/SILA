"""LLM rewrite engine — Phase 7 (reecriture contrainte qualite-first).

Utilise Qwen3.5-27B sur LXC 225 via API completions.
Strategie : 2 tentatives avec max_tokens croissant. Si le modele
pense trop, on strip <think>...</think> et on extrait la reponse.
"""

from __future__ import annotations

import logging
import os
import re

import httpx

from src.engines.rewrite.interface import RewriterInterface, RewriteResult

logger = logging.getLogger(__name__)

# Fast prompt — bypasses Qwen3.5 thinking mode by being concise
PROMPT_TEMPLATE = (
    "Shorten for voice dubbing ({min_chars}-{max_chars} chars). Only output the text.\n\n"
    "{text}\n\nShort:"
)

LANG_NAMES = {
    "en": "English", "fr": "French", "es": "Spanish",
    "de": "German", "it": "Italian", "pt": "Portuguese",
}


class LLMRewriteEngine(RewriterInterface):
    """Reecriture contrainte via LLM (API completions, Qwen3.5-27B)."""

    def __init__(
        self,
        api_base: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
        enable_thinking: bool = True,
    ):
        self.api_base = api_base or os.environ.get(
            "SILA_LLM_API_BASE", "http://192.168.1.225:8080"
        )
        self.model = model or os.environ.get(
            "SILA_LLM_MODEL", "Qwen3.5-27B-Q8_0.gguf"
        )
        self.timeout = timeout
        self.enable_thinking = enable_thinking
        self._client = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.api_base,
                timeout=self.timeout,
            )
        return self._client

    def _extract_answer(self, raw: str) -> str:
        """Extract the actual answer, stripping Qwen3.5 thinking."""
        if "</think>" in raw:
            return raw.split("</think>")[-1].strip()
        if "<think>" in raw:
            # Thinking started but never finished — discard all
            before = raw.split("<think>")[0].strip()
            if before:
                return before
            return ""
        return raw.strip()

    def _call_llm(self, prompt: str, max_tokens: int) -> str:
        """Single LLM call, returns extracted answer."""
        client = self._get_client()
        response = client.post(
            "/v1/completions",
            json={
                "model": self.model,
                "prompt": prompt,
                "temperature": 0.3,
                "max_tokens": max_tokens,
                "stop": ["Original:", "\n\nOriginal", "\n\n", "\nShort:"],
            },
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["text"]
        return self._extract_answer(raw)

    def rewrite(
        self,
        text: str,
        target_lang: str,
        max_chars: int,
        timing_budget_ms: int,
        context: str = "",
    ) -> RewriteResult:
        """Reecrit un texte pour le raccourcir sous max_chars.

        Strategie : essai avec max_tokens=150 (rapide), puis 500 si vide.
        """
        lang_name = LANG_NAMES.get(target_lang, target_lang)
        min_chars = int(max_chars * 0.80)
        prompt = ""
        if context:
            prompt = context
        prompt += PROMPT_TEMPLATE.format(
            lang=lang_name,
            min_chars=min_chars,
            max_chars=max_chars,
            text=text,
        )

        # Prepend /no_think if thinking disabled
        if not self.enable_thinking:
            prompt = "/no_think\n" + prompt

        try:
            # Attempt 1: low max_tokens (fast, works if model responds directly)
            rewritten = self._call_llm(prompt, max_tokens=100)

            # Attempt 2: higher tokens if first was empty (model was thinking)
            if not rewritten:
                logger.debug("Rewrite attempt 1 empty, retrying with more tokens")
                rewritten = self._call_llm(prompt, max_tokens=120)

            # Clean up: extract text between quotes if present, strip metadata
            import re as _re
            # Try to extract quoted text first (model often wraps in quotes)
            quoted = _re.search(r'"([^"]+)"', rewritten)
            if quoted and len(quoted.group(1)) > 10:
                rewritten = quoted.group(1).strip()
            elif rewritten.startswith('"') and rewritten.endswith('"'):
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
