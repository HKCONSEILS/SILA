"""Point d'entree CLI du pipeline SILA V1.

Voir MASTERPLAN.md §9 — python -m src.cli.main --input video.mp4 --target-lang en
Voir MASTERPLAN.md §12.3 — granularite de relance.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler

from src.pipeline.runner import run_pipeline

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


@click.command()
@click.option(
    "--input", "input_video",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Chemin vers la video source.",
)
@click.option(
    "--target-lang",
    default=None,
    help="Code ISO 639-1 de la langue cible (ex: en). Alias pour --target-langs avec 1 langue.",
)
@click.option(
    "--target-langs",
    default=None,
    help="Langues cibles separees par virgule (ex: en,es,de). Prioritaire sur --target-lang.",
)
@click.option(
    "--source-lang",
    default="fr",
    help="Code ISO 639-1 de la langue source (defaut: fr).",
)
@click.option(
    "--data-dir",
    default="data/projects",
    type=click.Path(path_type=Path),
    help="Repertoire racine des donnees projet.",
)
@click.option(
    "--project-id",
    default=None,
    help="ID du projet (genere automatiquement si non fourni).",
)
@click.option(
    "--from-stage",
    default=None,
    help="Reprendre depuis cette etape (ex: translate, tts, assembly).",
)
@click.option(
    "--retry",
    default=None,
    help="Mode de relance (ex: 'failed --lang en', 'segment seg_0042 --lang en').",
)
@click.option("--verbose", "-v", is_flag=True, help="Mode verbose (DEBUG).")
@click.option("--phrase-aware", is_flag=True, default=False, help="Enable phrase-aware segmentation (off by default).")
@click.option("--demucs", type=click.Choice(["on", "off", "auto"]), default="off",
    help="Demucs vocal separation: on, off (default), auto (SNR detection).")
@click.option("--diarize", is_flag=True, default=False, help="Enable multi-speaker diarization via pyannote (requires HF token).")
@click.option("--rewrite-endpoint", default=None, type=str,
    help="Endpoint API du LLM de rewrite (défaut: Qwen3.5 sur LXC 225). Ex: http://localhost:8081")
@click.option("--glossary", default=None, type=click.Path(exists=True, path_type=Path),
    help="Path to project glossary JSON file for terminology consistency.")
@click.option("--asr-engine", default="whisperx", type=click.Choice(["whisperx", "qwen3", "voxtral"]),
    help="ASR engine (default: whisperx). qwen3 and voxtral are stubs.")
@click.option("--force-reprocess", is_flag=True, default=False,
    help="Ignore cache and reprocess all segments (debug).")
@click.option("--multitrack", is_flag=True, default=False,
    help="Export separate audio tracks (voice, background, mix).")
@click.option("--captions", is_flag=True, default=False,
    help="Embed SRT subtitles into output MP4 (soft captions, mov_text).")
@click.option("--translate-rewrite-fusion", is_flag=True, default=False,
    help="Use Magistral for both translation and rewrite (skip NLLB). Requires --rewrite-endpoint.")
@click.option(
    "--tts-engine",
    default="cosyvoice",
    type=click.Choice(["cosyvoice", "voxtral", "moss"]),
    help="Moteur TTS (defaut: cosyvoice).",
)
def cli(
    input_video: Path,
    target_lang: str | None,
    target_langs: str | None,
    source_lang: str,
    data_dir: Path,
    project_id: str | None,
    from_stage: str | None,
    retry: str | None,
    verbose: bool,
    tts_engine: str,
    phrase_aware: bool,
    demucs: bool,
    diarize: bool,
    rewrite_endpoint: str | None,
    glossary: Path | None,
    asr_engine: str,
    force_reprocess: bool,
    multitrack: bool,
    captions: bool,
    translate_rewrite_fusion: bool,
) -> None:
    """SILA — Pipeline de traduction et doublage video multilingue."""
    _setup_logging(verbose)
    logger = logging.getLogger(__name__)

    console.print(f"[bold blue]SILA[/bold blue] — Pipeline V1", highlight=False)
    console.print(f"  Video: {input_video}")
    # Resolve target languages
    if target_langs:
        langs = [l.strip() for l in target_langs.split(",")]
    elif target_lang:
        langs = [target_lang]
    else:
        console.print("[bold red]Error[/bold red]: --target-lang or --target-langs required")
        sys.exit(1)
    target_lang = langs[0]  # For backward compat in pipeline signature
    console.print(f"  {source_lang} -> {', '.join(langs)}")

    try:
        if phrase_aware:
            import os
            os.environ["SILA_PHRASE_AWARE"] = "1"
            console.print("  Phrase-aware: ENABLED")
        console.print(f"  TTS engine: {tts_engine}")
        if demucs != "off":
            console.print(f"  Demucs: {demucs.upper()}")
        if diarize:
            console.print("  Diarize: ENABLED")
        if captions:
            console.print("  Captions: ENABLED (SRT embedded)")
        if rewrite_endpoint:
            console.print(f"  Rewrite endpoint: {rewrite_endpoint}")
        if translate_rewrite_fusion:
            console.print("  Translation: Magistral fusion (NLLB skipped)")
        if glossary:
            console.print(f"  Glossary: {glossary}")
        manifest = run_pipeline(
            video_path=input_video,
            source_lang=source_lang,
            target_lang=target_lang,
            target_langs=langs,
            data_dir=data_dir,
            project_id=project_id,
            from_stage=from_stage,
            tts_engine=tts_engine,
            demucs_enabled=(demucs == "on"),
            demucs_auto=(demucs == "auto"),
            diarize_enabled=diarize,
            rewrite_endpoint=rewrite_endpoint,
            glossary_path=str(glossary) if glossary else None,
            asr_engine=asr_engine,
            force_reprocess=force_reprocess,
            multitrack=multitrack,
            captions=captions,
            translate_rewrite_fusion=translate_rewrite_fusion,
        )
        project_id = manifest["project"]["project_id"]
        console.print(f"\n[bold green]Done[/bold green] — Project: {project_id}")
    except Exception as exc:
        logger.exception("Pipeline failed")
        console.print(f"\n[bold red]Error[/bold red]: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
