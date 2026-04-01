#!/usr/bin/env python3
"""MOSS-TTS inference server — persistent model on port 8082.

Loads MossTTSLocal-1.7B once at startup, serves synthesis requests via HTTP.
Same pattern as llama-ministral on port 8081.

Endpoints:
    POST /synthesize       — single segment synthesis
    POST /synthesize_batch — batch synthesis (multiple segments)
    GET  /health           — server status + VRAM usage

Usage:
    /opt/sila/bench/moss-tts-venv/bin/python /opt/sila/bench/moss_tts_server.py

See SILA_Masterplan.md §3.1 — MossTTSLocal 1.7B.
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

# Disable cuDNN SDP to avoid MOSS compatibility issues
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
DTYPE = torch.bfloat16
MODEL_ID = "OpenMOSS-Team/MOSS-TTS-Local-Transformer"
MOSS_SAMPLE_RATE = 24000

logger.info("Loading MOSS-TTS model: %s ...", MODEL_ID)
_t0 = time.time()

from transformers import AutoModel, AutoProcessor  # noqa: E402

processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
processor.audio_tokenizer = processor.audio_tokenizer.to(DEVICE)
model = AutoModel.from_pretrained(
    MODEL_ID, trust_remote_code=True, torch_dtype=DTYPE
).to(DEVICE)
model.eval()

_load_time = time.time() - _t0
_vram_gb = torch.cuda.max_memory_allocated() / 1e9
logger.info("MOSS-TTS loaded in %.1fs. VRAM: %.2f Go", _load_time, _vram_gb)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="MOSS-TTS Server", version="1.0.0")


# --- Request / Response schemas ---


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


# --- Inference helper ---


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

    # Build inputs
    inputs = processor(text=text, return_tensors="pt")
    inputs = {k: v.to(DEVICE) for k, v in inputs.items()}

    # Optional: load reference audio for voice cloning
    if reference and Path(reference).exists():
        ref_wav, ref_sr = torchaudio.load(reference)
        if ref_sr != MOSS_SAMPLE_RATE:
            ref_wav = torchaudio.functional.resample(ref_wav, ref_sr, MOSS_SAMPLE_RATE)
        ref_wav = ref_wav.to(DEVICE)
        inputs["prompt_input_ids"] = processor.audio_tokenizer.encode(ref_wav)

    # Generate with optional duration control via max_new_tokens
    gen_kwargs = {}
    if tokens is not None and tokens > 0:
        gen_kwargs["max_new_tokens"] = tokens

    with torch.no_grad():
        output = model.generate(**inputs, **gen_kwargs)

    # Decode audio tokens to waveform
    if hasattr(output, "audio_values"):
        wav = output.audio_values.squeeze().cpu()
    elif hasattr(output, "waveform"):
        wav = output.waveform.squeeze().cpu()
    else:
        # Fallback: decode via processor
        wav = processor.audio_tokenizer.decode(output).squeeze().cpu()

    # Save WAV at native 24kHz (resample to 48kHz stays in SILA engine)
    if wav.dim() == 1:
        wav = wav.unsqueeze(0)
    torchaudio.save(str(out_path), wav, MOSS_SAMPLE_RATE)

    duration_ms = int(wav.shape[-1] / MOSS_SAMPLE_RATE * 1000)
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


# --- Endpoints ---


@app.post("/synthesize", response_model=SynthesizeResponse)
def synthesize(req: SynthesizeRequest) -> SynthesizeResponse:
    """Synthesize a single text segment to WAV."""
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
    """Synthesize multiple segments sequentially."""
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
    """Server health check."""
    vram = torch.cuda.max_memory_allocated() / 1e9
    return HealthResponse(
        status="ok",
        model="MossTTSLocal-1.7B",
        vram_gb=round(vram, 2),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8082, log_level="info")
