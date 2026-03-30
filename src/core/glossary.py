"""Project glossary for terminology consistency in translation and rewrite.

Voir MASTERPLAN.md §5.3, §6.2 — glossary injection.
Supports post-translation term replacement and rewrite prompt injection.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def load_glossary(glossary_path: str | Path) -> dict:
    """Load a glossary JSON file.

    Args:
        glossary_path: Path to glossary JSON.

    Returns:
        Glossary dict with 'entries' list.
    """
    with open(glossary_path) as f:
        glossary = json.load(f)
    logger.info("Loaded glossary: %s (%d entries)", glossary.get("project", "unknown"), len(glossary.get("entries", [])))
    return glossary


def apply_glossary_post_translation(
    translated_text: str,
    source_text: str,
    glossary: dict,
    target_lang: str,
) -> tuple[str, list[str]]:
    """Post-translation: replace incorrectly translated glossary terms.

    For each glossary entry present in the source text, ensures the
    correct translation appears in the output. Handles case-insensitive
    matching for acronyms and proper nouns.

    Args:
        translated_text: Text after NLLB translation.
        source_text: Original source text.
        glossary: Loaded glossary dict.
        target_lang: Target language code.

    Returns:
        Tuple of (corrected_text, list of glossary_hits).
    """
    hits = []
    result = translated_text

    for entry in glossary.get("entries", []):
        source_term = entry["source"]
        target_term = entry.get("translations", {}).get(target_lang)

        if not target_term:
            continue

        # Check if source term appears in source text (case-insensitive)
        if not re.search(re.escape(source_term), source_text, re.IGNORECASE):
            continue

        # Term is in source — ensure correct translation in output
        # For terms that should NOT be translated (proper nouns, acronyms),
        # the target_term is often the same as source_term
        if target_term.lower() == source_term.lower():
            # Term should be preserved as-is — check if it's already there
            if re.search(re.escape(target_term), result, re.IGNORECASE):
                hits.append(source_term)
                continue
            # Not found — it may have been incorrectly translated
            # We can't easily find what it was translated to, so just log it
            hits.append(f"{source_term}(missing)")
        else:
            # Term has a specific translation — replace if source term appears literally
            if source_term in result:
                result = result.replace(source_term, target_term)
                hits.append(source_term)
            elif re.search(re.escape(source_term), result, re.IGNORECASE):
                result = re.sub(re.escape(source_term), target_term, result, flags=re.IGNORECASE)
                hits.append(source_term)

    if hits:
        logger.info("Glossary hits: %s", hits)

    return result, hits


def build_glossary_prompt_section(glossary: dict, target_lang: str, source_text: str) -> str:
    """Build a glossary instruction section for the rewrite prompt.

    Only includes entries whose source term appears in the text.

    Args:
        glossary: Loaded glossary dict.
        target_lang: Target language code.
        source_text: Original source text to check for relevant terms.

    Returns:
        Prompt section string, empty if no relevant terms.
    """
    relevant = []
    for entry in glossary.get("entries", []):
        source_term = entry["source"]
        target_term = entry.get("translations", {}).get(target_lang, source_term)
        if re.search(re.escape(source_term), source_text, re.IGNORECASE):
            relevant.append(f"  {source_term} → {target_term}")

    if not relevant:
        return ""

    return "Glossary — preserve these terms exactly:\n" + "\n".join(relevant) + "\n\n"
