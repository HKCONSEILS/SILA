"""SILA API — minimal FastAPI wrapper around the CLI pipeline.

4 routes: POST /jobs, GET /jobs, GET /jobs/{id}, GET /jobs/{id}/download/{lang}
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

app = FastAPI(title="SILA API", version="2.0")

PROJECTS_DIR = os.environ.get("SILA_PROJECTS_DIR", "/opt/sila/projects")
APP_DIR = "/opt/sila/app"


def _run_pipeline(cmd: list[str], job_dir: str):
    """Run pipeline as subprocess, log to job directory."""
    log_path = os.path.join(job_dir, "pipeline.log")
    with open(log_path, "w") as log:
        subprocess.run(cmd, cwd=APP_DIR, stdout=log, stderr=subprocess.STDOUT)


@app.post("/jobs")
async def create_job(
    video: UploadFile = File(...),
    target_langs: str = "en",
    demucs: str = "auto",
    diarize: bool = False,
    glossary: str | None = None,
    rewrite_endpoint: str | None = None,
    background_tasks: BackgroundTasks = None,
):
    """Upload a video and start the dubbing pipeline."""
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job_dir = os.path.join(PROJECTS_DIR, job_id)
    os.makedirs(os.path.join(job_dir, "source"), exist_ok=True)

    input_path = os.path.join(job_dir, "source", "input.mp4")
    with open(input_path, "wb") as f:
        shutil.copyfileobj(video.file, f)

    cmd = [
        "python", "-m", "src.cli.main",
        "--input", input_path,
        "--target-langs", target_langs,
        "--demucs", demucs,
        "--data-dir", PROJECTS_DIR,
        "--project-id", job_id,
    ]
    if diarize:
        cmd.append("--diarize")
    if glossary:
        cmd.extend(["--glossary", glossary])
    if rewrite_endpoint:
        cmd.extend(["--rewrite-endpoint", rewrite_endpoint])

    background_tasks.add_task(_run_pipeline, cmd, job_dir)

    return {"job_id": job_id, "status": "started", "target_langs": target_langs.split(",")}


@app.get("/jobs")
async def list_jobs():
    """List all pipeline jobs."""
    jobs = []
    for manifest_path in sorted(glob.glob(f"{PROJECTS_DIR}/*/manifest.json")):
        try:
            with open(manifest_path) as f:
                m = json.load(f)
            job_dir = os.path.dirname(manifest_path)
            job_id = os.path.basename(job_dir)
            jobs.append({
                "job_id": job_id,
                "status": m.get("project", {}).get("status", "unknown"),
                "target_langs": list(m.get("outputs", {}).keys()),
                "duration_ms": m.get("project", {}).get("duration_ms", 0),
            })
        except Exception:
            continue
    return {"jobs": jobs, "count": len(jobs)}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """Get detailed job status with progress."""
    manifest_path = os.path.join(PROJECTS_DIR, job_id, "manifest.json")
    if not os.path.exists(manifest_path):
        raise HTTPException(404, "Job not found")

    with open(manifest_path) as f:
        m = json.load(f)

    segments = m.get("segments", [])
    progress = {}
    for lang in m.get("project", {}).get("target_langs", []):
        tts_manifest = os.path.join(PROJECTS_DIR, job_id, "tts", lang, "tts_manifest.json")
        if os.path.exists(tts_manifest):
            with open(tts_manifest) as f:
                tts = json.load(f)
            progress[lang] = {"completed": len(tts), "total": len(segments)}
        else:
            progress[lang] = {"completed": 0, "total": len(segments)}

    return {
        "job_id": job_id,
        "status": m.get("project", {}).get("status", "unknown"),
        "stages": m.get("stages", {}),
        "progress": progress,
        "outputs": m.get("outputs", {}),
        "metrics": m.get("metrics", {}),
    }


@app.get("/jobs/{job_id}/download/{lang}")
async def download(job_id: str, lang: str):
    """Download the dubbed MP4 for a given language."""
    mp4_path = os.path.join(PROJECTS_DIR, job_id, "exports", f"output_{lang}.mp4")
    if not os.path.exists(mp4_path):
        raise HTTPException(404, f"Export not found for language '{lang}'")
    return FileResponse(mp4_path, filename=f"{job_id}_{lang}.mp4", media_type="video/mp4")
