#!/usr/bin/env python3
"""SILA Pipeline Metrics Viewer.

Usage: python scripts/show_metrics.py /path/to/project/
"""

import json
import sys
from pathlib import Path


def show_metrics(project_dir: str):
    metrics_path = Path(project_dir) / "metrics.jsonl"
    if not metrics_path.exists():
        print(f"No metrics.jsonl in {project_dir}")
        sys.exit(1)

    metrics = []
    with open(metrics_path) as f:
        for line in f:
            line = line.strip()
            if line:
                metrics.append(json.loads(line))

    if not metrics:
        print("No metrics recorded.")
        return

    ram_vals = [m["ram_mb"] for m in metrics]
    gpu_vals = [m.get("gpu_mem_mb", 0) for m in metrics if m.get("gpu_mem_mb")]

    # Phase breakdown
    phases = {}
    for m in metrics:
        p = m["phase"]
        if p not in phases:
            phases[p] = {"start": m["elapsed_s"], "end": m["elapsed_s"], "count": 0}
        phases[p]["end"] = m["elapsed_s"]
        phases[p]["count"] += 1

    W = 52
    print("+" + "=" * W + "+")
    print("|" + "SILA PIPELINE METRICS".center(W) + "|")
    print("|" + f"Project: {Path(project_dir).name}".center(W) + "|")
    print("+" + "-" * W + "+")
    print(f"| Total duration: {metrics[-1]['elapsed_s']:.1f}s".ljust(W + 1) + "|")
    print(f"| Metrics recorded: {len(metrics)}".ljust(W + 1) + "|")
    print(f"| RAM peak: {max(ram_vals)} MB".ljust(W + 1) + "|")
    print(f"| RAM mean: {sum(ram_vals)//len(ram_vals)} MB".ljust(W + 1) + "|")
    if gpu_vals:
        print(f"| GPU peak: {max(gpu_vals)} MB".ljust(W + 1) + "|")
        print(f"| GPU mean: {sum(gpu_vals)//len(gpu_vals)} MB".ljust(W + 1) + "|")
    print("+" + "-" * W + "+")
    print("| Phase breakdown:".ljust(W + 1) + "|")
    for p, data in phases.items():
        dur = data["end"] - data["start"]
        line = f"|   {p:<20s} {dur:7.1f}s  ({data['count']} events)"
        print(line.ljust(W + 1) + "|")
    print("+" + "=" * W + "+")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/show_metrics.py /path/to/project/")
        sys.exit(1)
    show_metrics(sys.argv[1])
