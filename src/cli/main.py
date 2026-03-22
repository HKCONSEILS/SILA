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
    required=True,
    help="Code ISO 639-1 de la langue cible (ex: en, es, fr).",
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
def cli(
    input_video: Path,
    target_lang: str,
    source_lang: str,
    data_dir: Path,
    project_id: str | None,
    from_stage: str | None,
    retry: str | None,
    verbose: bool,
) -> None:
    """SILA — Pipeline de traduction et doublage video multilingue."""
    _setup_logging(verbose)
    logger = logging.getLogger(__name__)

    console.print(f"[bold blue]SILA[/bold blue] — Pipeline V1", highlight=False)
    console.print(f"  Video: {input_video}")
    console.print(f"  {source_lang} -> {target_lang}")

    try:
        manifest = run_pipeline(
            video_path=input_video,
            source_lang=source_lang,
            target_lang=target_lang,
            data_dir=data_dir,
            project_id=project_id,
            from_stage=from_stage,
        )
        project_id = manifest["project"]["project_id"]
        console.print(f"\n[bold green]Done[/bold green] — Project: {project_id}")
    except Exception as exc:
        logger.exception("Pipeline failed")
        console.print(f"\n[bold red]Error[/bold red]: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
