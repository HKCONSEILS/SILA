"""Pipeline metrics with JSONL logging.

Records RAM, GPU, and timing metrics at key pipeline points.
Writes to metrics.jsonl in the project directory.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)


class PipelineMetrics:
    """Records pipeline metrics to JSONL file."""

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir)
        self.metrics_path = self.project_dir / "metrics.jsonl"
        self.start_time = time.time()

    def record(self, phase: str, event: str, **kwargs):
        """Record a metric entry."""
        mem = psutil.virtual_memory()
        metric = {
            "timestamp": time.time(),
            "elapsed_s": round(time.time() - self.start_time, 1),
            "phase": phase,
            "event": event,
            "ram_mb": mem.used // (1024 * 1024),
            "ram_pct": round(mem.percent, 1),
        }

        # GPU metrics
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
                 "--format=csv,noheader,nounits", "-i", "2"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(", ")
                metric["gpu_mem_mb"] = int(parts[0])
                metric["gpu_mem_total_mb"] = int(parts[1])
                metric["gpu_util_pct"] = int(parts[2])
        except Exception:
            pass

        metric.update(kwargs)

        with open(self.metrics_path, "a") as f:
            f.write(json.dumps(metric) + "\n")

    def summary(self) -> dict:
        """Produce summary of all recorded metrics."""
        if not self.metrics_path.exists():
            return {}

        metrics = []
        with open(self.metrics_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    metrics.append(json.loads(line))

        if not metrics:
            return {}

        ram_vals = [m["ram_mb"] for m in metrics]
        gpu_vals = [m.get("gpu_mem_mb", 0) for m in metrics if m.get("gpu_mem_mb")]

        # Time per phase
        phase_times = {}
        for m in metrics:
            phase = m["phase"]
            if phase not in phase_times:
                phase_times[phase] = {"start": m["elapsed_s"], "end": m["elapsed_s"]}
            phase_times[phase]["end"] = m["elapsed_s"]
        for p in phase_times:
            phase_times[p]["duration_s"] = round(phase_times[p]["end"] - phase_times[p]["start"], 1)

        return {
            "total_duration_s": round(metrics[-1]["elapsed_s"], 1),
            "total_metrics": len(metrics),
            "ram_peak_mb": max(ram_vals),
            "ram_mean_mb": round(sum(ram_vals) / len(ram_vals)),
            "gpu_peak_mb": max(gpu_vals) if gpu_vals else None,
            "gpu_mean_mb": round(sum(gpu_vals) / len(gpu_vals)) if gpu_vals else None,
            "phases": phase_times,
        }
