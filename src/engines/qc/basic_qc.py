"""Enhanced QC engine — Phase 10.

V1.1: timing checks + loudness + true peak + duration.
Voir MASTERPLAN.md §6.1 Phase 10.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

from src.core.timing import classify_timing_fit
from src.engines.qc.interface import QCInterface, QCResult

logger = logging.getLogger(__name__)


class BasicQCEngine(QCInterface):
    """QC V1.1 : timing + loudness + true peak + duration."""

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

    def check_mix(self, mix_path: Path, source_duration_ms: int) -> dict:
        """Verifie le mix final (loudness, true peak, duree)."""
        checks = {}

        # Loudness + true peak via FFmpeg
        cmd = [
            "ffmpeg", "-i", str(mix_path),
            "-af", "loudnorm=I=-16:TP=-1:LRA=11:print_format=json",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        match = re.search(r'\{[^{}]*"input_i"[^{}]*\}', result.stderr, re.DOTALL)
        stats = json.loads(match.group()) if match else {}

        measured_i = float(stats.get("input_i", -99))
        measured_tp = float(stats.get("input_tp", 0))

        checks["loudness"] = {
            "status": "PASS" if abs(measured_i - (-16)) <= 1.0 else "WARNING",
            "measured_lufs": round(measured_i, 1),
            "target_lufs": -16,
        }
        checks["true_peak"] = {
            "status": "PASS" if measured_tp < -1.0 else "WARNING",
            "measured_dbtp": round(measured_tp, 1),
            "max_dbtp": -1.0,
        }

        # Duration check
        import soundfile as sf
        mix_info = sf.info(str(mix_path))
        mix_ms = int(mix_info.duration * 1000)
        dev_pct = abs(mix_ms - source_duration_ms) / source_duration_ms * 100

        checks["duration"] = {
            "status": "PASS" if dev_pct < 2.0 else "WARNING",
            "source_ms": source_duration_ms,
            "mix_ms": mix_ms,
            "deviation_pct": round(dev_pct, 2),
        }

        return checks

    def generate_report(
        self,
        segments_qc: list[dict],
        output_path: Path,
        mix_checks: dict | None = None,
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

        if mix_checks:
            report["mix_checks"] = mix_checks
            statuses = [v["status"] for v in mix_checks.values()]
            report["mix_overall"] = "FAIL" if "FAIL" in statuses else ("WARNING" if "WARNING" in statuses else "PASS")

        report["can_export"] = fit_ok > 0 and report.get("mix_overall", "PASS") != "FAIL"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)

        logger.info("QC report: %d/%d OK (%.0f%%)", fit_ok, total, report["pass_rate"] * 100)
        return report
