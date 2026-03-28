"""Basic QC engine — Phase 10.

V1: timing checks only (no UTMOS).
Voir MASTERPLAN.md §6.1 Phase 10.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from src.core.timing import classify_timing_fit
from src.engines.qc.interface import QCInterface, QCResult

logger = logging.getLogger(__name__)


class BasicQCEngine(QCInterface):
    """QC basique V1 : verification timing uniquement."""

    def check(self, audio_path: Path, reference_duration_ms: int) -> QCResult:
        """Verifie un segment audio."""
        import soundfile as sf

        if not audio_path.exists():
            return QCResult(
                duration_ms=0,
                timing_delta_ms=-reference_duration_ms,
                flags=["missing_audio"],
            )

        info = sf.info(str(audio_path))
        actual_ms = int(info.duration * 1000)
        delta = actual_ms - reference_duration_ms
        fit = classify_timing_fit(actual_ms, reference_duration_ms)

        flags = []
        if abs(delta) > reference_duration_ms * 0.15:
            flags.append(f"timing_drift_{fit.value}")

        return QCResult(
            duration_ms=actual_ms,
            timing_delta_ms=delta,
            flags=flags,
        )

    def generate_report(
        self,
        segments_qc: list[dict],
        output_path: Path,
    ) -> dict:
        """Genere le rapport QC complet."""
        total = len(segments_qc)
        fit_ok = sum(1 for s in segments_qc if not s.get("flags"))
        timing_issues = sum(1 for s in segments_qc if any("timing" in f for f in s.get("flags", [])))
        missing = sum(1 for s in segments_qc if any("missing" in f for f in s.get("flags", [])))

        report = {
            "total_segments": total,
            "fit_ok": fit_ok,
            "timing_issues": timing_issues,
            "missing_audio": missing,
            "pass_rate": fit_ok / total if total > 0 else 0.0,
            "segments": segments_qc,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)

        logger.info("QC report: %d/%d OK (%.0f%%)", fit_ok, total, report["pass_rate"] * 100)
        return report
