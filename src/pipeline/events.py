"""Pipeline event bus for real-time WebSocket progress streaming.

Thread-safe: pipeline runs in a thread, WebSocket clients read from asyncio.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class PipelineEventBus:
    """Bus d'événements thread-safe pour streamer la progression."""

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._lock = threading.Lock()

    def subscribe(self, job_id: str) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.setdefault(job_id, []).append(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue):
        with self._lock:
            if job_id in self._subscribers:
                self._subscribers[job_id] = [q for q in self._subscribers[job_id] if q is not queue]

    def emit(self, job_id: str, event_type: str, **data: Any):
        event = {
            "type": event_type,
            "job_id": job_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        with self._lock:
            for queue in self._subscribers.get(job_id, []):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    def phase_started(self, job_id: str, phase: str, **kw):
        self.emit(job_id, "phase_started", phase=phase, **kw)

    def phase_completed(self, job_id: str, phase: str, **kw):
        self.emit(job_id, "phase_completed", phase=phase, **kw)

    def segment_done(self, job_id: str, segment_id: str, lang: str, duration_ms: int, budget_ms: int, **kw):
        self.emit(job_id, "segment_done", segment_id=segment_id, lang=lang,
                  duration_ms=duration_ms, budget_ms=budget_ms, **kw)

    def progress(self, job_id: str, done: int, total: int, phase: str, **kw):
        pct = round(done / total * 100, 1) if total else 0
        self.emit(job_id, "progress", done=done, total=total, phase=phase, pct=pct, **kw)

    def error(self, job_id: str, message: str, **kw):
        self.emit(job_id, "error", message=message, **kw)

    def job_completed(self, job_id: str, **kw):
        self.emit(job_id, "job_completed", **kw)


# Singleton
event_bus = PipelineEventBus()
