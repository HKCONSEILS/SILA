"""NLLB-200 CTranslate2 translation engine — Phase 6.

Voir MASTERPLAN.md §3.1 — NLLB-200 3.3B (CTranslate2).
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.engines.mt.interface import MTInterface, TranslationResult

logger = logging.getLogger(__name__)

# Mapping ISO 639-1 -> NLLB language codes
LANG_MAP = {
    "fr": "fra_Latn",
    "en": "eng_Latn",
    "es": "spa_Latn",
    "de": "deu_Latn",
    "it": "ita_Latn",
    "pt": "por_Latn",
    "nl": "nld_Latn",
    "ru": "rus_Cyrl",
    "zh": "zho_Hans",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "ar": "arb_Arab",
    "hi": "hin_Deva",
    "tr": "tur_Latn",
    "pl": "pol_Latn",
    "sv": "swe_Latn",
    "da": "dan_Latn",
    "no": "nob_Latn",
    "fi": "fin_Latn",
    "uk": "ukr_Cyrl",
    "cs": "ces_Latn",
    "ro": "ron_Latn",
    "hu": "hun_Latn",
    "el": "ell_Grek",
    "vi": "vie_Latn",
    "th": "tha_Thai",
    "id": "ind_Latn",
}


class NLLBEngine(MTInterface):
    """NLLB-200 via CTranslate2."""

    def __init__(self, model_dir: str | Path, device: str = "cuda"):
        self.model_dir = str(model_dir)
        self.device = device
        self._translator = None
        self._tokenizer = None

    def _load(self):
        if self._translator is not None:
            return
        import ctranslate2
        from transformers import AutoTokenizer

        logger.info("Loading NLLB-200 from %s on %s...", self.model_dir, self.device)
        self._translator = ctranslate2.Translator(
            self.model_dir,
            device=self.device,
            compute_type="float16" if self.device == "cuda" else "int8",
        )
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_dir)
        logger.info("NLLB-200 loaded.")

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> TranslationResult:
        """Traduit un texte."""
        self._load()

        src_code = LANG_MAP.get(source_lang, source_lang)
        tgt_code = LANG_MAP.get(target_lang, target_lang)

        self._tokenizer.src_lang = src_code
        tokens = self._tokenizer.convert_ids_to_tokens(
            self._tokenizer.encode(text)
        )

        results = self._translator.translate_batch(
            [tokens],
            target_prefix=[[tgt_code]],
            beam_size=4,
            max_input_length=512,
            max_decoding_length=512,
        )

        output_tokens = results[0].hypotheses[0]
        # Remove target language token
        if output_tokens and output_tokens[0] == tgt_code:
            output_tokens = output_tokens[1:]

        translated = self._tokenizer.decode(
            self._tokenizer.convert_tokens_to_ids(output_tokens),
            skip_special_tokens=True,
        )

        return TranslationResult(
            text=translated,
            estimated_chars=len(translated),
            confidence=results[0].scores[0] if results[0].scores else 0.0,
        )

    def unload(self):
        """Libere la VRAM."""
        import gc
        self._translator = None
        self._tokenizer = None
        gc.collect()
        logger.info("NLLB-200 unloaded.")
