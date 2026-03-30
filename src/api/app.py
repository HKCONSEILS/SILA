"""SILA API — FastAPI with WebSocket real-time progress.

Routes: POST /jobs, GET /jobs, GET /jobs/{id}, GET /jobs/{id}/download/{lang}
WebSocket: ws://host:8000/ws/jobs/{id}
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import threading
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

app = FastAPI(title="SILA API", version="3.0")

PROJECTS_DIR = os.environ.get("SILA_PROJECTS_DIR", "/opt/sila/projects")
APP_DIR = "/opt/sila/app"

# Ensure app is in path for imports
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from src.pipeline.events import event_bus


def _run_pipeline_thread(job_id: str, input_path: str, target_langs: str,
                         demucs: str, diarize: bool, glossary: str | None,
                         rewrite_endpoint: str | None):
    """Run pipeline in a thread (shares memory with FastAPI for event bus)."""
    try:
        from src.pipeline.runner import run_pipeline

        langs = [l.strip() for l in target_langs.split(",")]
        event_bus.phase_started(job_id, "pipeline")

        run_pipeline(
            video_path=Path(input_path),
            source_lang="fr",
            target_lang=langs[0],
            target_langs=langs,
            data_dir=Path(PROJECTS_DIR),
            project_id=job_id,
            demucs_enabled=(demucs == "on"),
            demucs_auto=(demucs == "auto"),
            rewrite_endpoint=rewrite_endpoint,
            job_id=job_id,
        )
    except Exception as e:
        event_bus.error(job_id, str(e))


@app.post("/jobs")
async def create_job(
    video: UploadFile = File(...),
    target_langs: str = "en",
    source_lang: str = "fr",
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

    thread = threading.Thread(
        target=_run_pipeline_thread,
        args=(job_id, input_path, target_langs, demucs, diarize, glossary, rewrite_endpoint),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id, "status": "started", "target_langs": target_langs.split(",")}


@app.websocket("/ws/jobs/{job_id}")
async def websocket_job_progress(websocket: WebSocket, job_id: str):
    """WebSocket for real-time pipeline progress.

    Events: phase_started, phase_completed, segment_done, progress, job_completed, error.
    """
    await websocket.accept()
    queue = event_bus.subscribe(job_id)

    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("type") in ("job_completed", "error"):
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        event_bus.unsubscribe(job_id, queue)


@app.get("/jobs")
async def list_jobs():
    """List all pipeline jobs."""
    jobs = []
    for manifest_path in sorted(glob.glob(f"{PROJECTS_DIR}/*/manifest.json")):
        try:
            with open(manifest_path) as f:
                m = json.load(f)
            job_id = os.path.basename(os.path.dirname(manifest_path))
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


@app.get("/jobs/{job_id}/segments")
async def get_segments(job_id: str, lang: str = "en"):
    """Return all segments with review data."""
    manifest_path = os.path.join(PROJECTS_DIR, job_id, "manifest.json")
    if not os.path.exists(manifest_path):
        raise HTTPException(404, "Job not found")

    with open(manifest_path) as f:
        m = json.load(f)

    # Load translations
    trans_path = os.path.join(PROJECTS_DIR, job_id, "asr", f"translations_{lang}.json")
    translations = {}
    if os.path.exists(trans_path):
        with open(trans_path) as f:
            for t in json.load(f):
                translations[t["segment_id"]] = t

    # Load TTS manifest
    tts_path = os.path.join(PROJECTS_DIR, job_id, "tts", lang, "tts_manifest.json")
    tts_data = {}
    if os.path.exists(tts_path):
        with open(tts_path) as f:
            for t in json.load(f):
                tts_data[t["segment_id"]] = t

    segments = []
    for seg in m.get("segments", []):
        sid = seg["segment_id"]
        trans = translations.get(sid, {})
        tts = tts_data.get(sid, {})
        budget = seg["timing_budget_ms"]
        dur = tts.get("duration_ms", 0)
        delta = (dur - budget) / budget * 100 if budget and dur else 0

        segments.append({
            "segment_id": sid,
            "speaker_id": seg.get("speaker_id"),
            "start_ms": seg["start_ms"],
            "end_ms": seg["end_ms"],
            "source_text": seg.get("source_text", ""),
            "translated_text": trans.get("translated_text", ""),
            "timing_budget_ms": budget,
            "tts_duration_ms": dur,
            "delta_pct": round(delta, 1),
            "dnsmos": tts.get("dnsmos"),
            "status": "PASS" if abs(delta) <= 15 else "FAIL",
            "has_audio": bool(tts.get("audio_path")),
        })

    return {"segments": segments, "total": len(segments)}


@app.get("/jobs/{job_id}/segments/{segment_id}/audio/{lang}")
async def get_segment_audio(job_id: str, segment_id: str, lang: str):
    """Serve a TTS segment WAV file."""
    tts_path = os.path.join(PROJECTS_DIR, job_id, "tts", lang, "tts_manifest.json")
    if not os.path.exists(tts_path):
        raise HTTPException(404, "TTS manifest not found")

    with open(tts_path) as f:
        for t in json.load(f):
            if t["segment_id"] == segment_id:
                audio_path = t.get("audio_path", "")
                if os.path.exists(audio_path):
                    return FileResponse(audio_path, media_type="audio/wav")
                break

    raise HTTPException(404, "Audio not found")


@app.get("/jobs/{job_id}/download/{lang}")
async def download(job_id: str, lang: str):
    """Download the dubbed MP4."""
    mp4_path = os.path.join(PROJECTS_DIR, job_id, "exports", f"output_{lang}.mp4")
    if not os.path.exists(mp4_path):
        raise HTTPException(404, f"Export not found for language '{lang}'")
    return FileResponse(mp4_path, filename=f"{job_id}_{lang}.mp4", media_type="video/mp4")


# Serve React frontend (built files)
from fastapi.staticfiles import StaticFiles

_frontend_dir = os.path.join(os.path.dirname(__file__), "../../frontend/dist")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
