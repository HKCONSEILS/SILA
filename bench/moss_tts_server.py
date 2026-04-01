#!/usr/bin/env python3
"""MOSS-TTS inference server — persistent model on port 8082.

Loads MossTTSLocal-1.7B once at startup, serves synthesis requests via HTTP.
Same pattern as llama-ministral on port 8081.

Uses the same inference logic as moss_tts_infer.py:
  processor.build_user_message() -> processor(conversations, mode='generation')
  -> model.generate() -> processor.decode()

Endpoints:
    POST /synthesize       — single segment synthesis
    POST /synthesize_batch — batch synthesis (multiple segments)
    GET  /health           — server status + VRAM usage

Usage:
    /opt/sila/bench/moss-tts-venv/bin/python /opt/sila/bench/moss_tts_server.py
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import torch
import torchaudio
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

torch.backends.cuda.enable_cudnn_sdp(False)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("moss-tts-server")

# ---------------------------------------------------------------------------
# Model loading (once at startup)
# ---------------------------------------------------------------------------
DEVICE = "cuda"
MODEL_ID = "OpenMOSS-Team/MOSS-TTS-Local-Transformer"

logger.info("Loading MOSS-TTS model: %s ...", MODEL_ID)
_t0 = time.time()

from transformers import AutoModel, AutoProcessor  # noqa: E402

processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
model = AutoModel.from_pretrained(
    MODEL_ID, trust_remote_code=True,
    dtype=torch.float16, device_map="auto", low_cpu_mem_usage=True,
)
model.eval()

MOSS_SAMPLE_RATE = processor.model_config.sampling_rate

_load_time = time.time() - _t0
_vram_gb = torch.cuda.max_memory_allocated() / 1e9
logger.info(
    "MOSS-TTS loaded in %.1fs. VRAM: %.2f Go. Sample rate: %d Hz",
    _load_time, _vram_gb, MOSS_SAMPLE_RATE,
)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="MOSS-TTS Server", version="1.0.0")


class SynthesizeRequest(BaseModel):
    text: str
    output_path: str
    tokens: Optional[int] = None
    reference: Optional[str] = None


class SynthesizeResponse(BaseModel):
    duration_ms: int
    sample_rate: int
    inference_time_s: float


class BatchSegment(BaseModel):
    id: str
    text: str
    output_path: str
    tokens: Optional[int] = None
    reference: Optional[str] = None


class BatchRequest(BaseModel):
    segments: list[BatchSegment]


class BatchSegmentResult(BaseModel):
    id: str
    duration_ms: int
    inference_time_s: float


class BatchResponse(BaseModel):
    results: list[BatchSegmentResult]


class HealthResponse(BaseModel):
    status: str
    model: str
    vram_gb: float


# ---------------------------------------------------------------------------
# Inference — same logic as moss_tts_infer.py
# ---------------------------------------------------------------------------


def _synthesize_one(
    text: str,
    output_path: str,
    tokens: int | None = None,
    reference: str | None = None,
) -> SynthesizeResponse:
    """Run MOSS-TTS inference for a single segment."""
    t0 = time.time()
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Build user message (same as moss_tts_infer.py)
    msg_kwargs = {"text": text}
    if tokens is not None and tokens > 0:
        msg_kwargs["tokens"] = tokens
    if reference and Path(reference).exists():
        msg_kwargs["reference"] = [reference]

    conversations = [[processor.build_user_message(**msg_kwargs)]]
    batch = processor(conversations, mode="generation")

    # Generate
    with torch.no_grad():
        outputs = model.generate(
            input_ids=batch["input_ids"].to(DEVICE),
            attention_mask=batch["attention_mask"].to(DEVICE),
            max_new_tokens=4096,
        )

    # Decode and save
    duration_ms = 0
    for msg in processor.decode(outputs):
        audio = msg.audio_codes_list[0]
        torchaudio.save(str(out_path), audio.unsqueeze(0), MOSS_SAMPLE_RATE)
        duration_ms = int(audio.shape[-1] / MOSS_SAMPLE_RATE * 1000)

    inference_s = round(time.time() - t0, 3)

    logger.info(
        "synthesize: tokens=%s, duration=%dms, inference=%.2fs, path=%s",
        tokens, duration_ms, inference_s, out_path.name,
    )

    return SynthesizeResponse(
        duration_ms=duration_ms,
        sample_rate=MOSS_SAMPLE_RATE,
        inference_time_s=inference_s,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/synthesize", response_model=SynthesizeResponse)
def synthesize(req: SynthesizeRequest) -> SynthesizeResponse:
    try:
        return _synthesize_one(
            text=req.text,
            output_path=req.output_path,
            tokens=req.tokens,
            reference=req.reference,
        )
    except Exception as exc:
        logger.exception("synthesize failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/synthesize_batch", response_model=BatchResponse)
def synthesize_batch(req: BatchRequest) -> BatchResponse:
    results = []
    for seg in req.segments:
        try:
            r = _synthesize_one(
                text=seg.text,
                output_path=seg.output_path,
                tokens=seg.tokens,
                reference=seg.reference,
            )
            results.append(BatchSegmentResult(
                id=seg.id,
                duration_ms=r.duration_ms,
                inference_time_s=r.inference_time_s,
            ))
        except Exception as exc:
            logger.error("batch segment %s failed: %s", seg.id, exc)
            raise HTTPException(
                status_code=500,
                detail=f"Segment {seg.id} failed: {exc}",
            )
    return BatchResponse(results=results)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    vram = torch.cuda.max_memory_allocated() / 1e9
    return HealthResponse(
        status="ok",
        model="MossTTSLocal-1.7B",
        vram_gb=round(vram, 2),
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8082, log_level="info")
