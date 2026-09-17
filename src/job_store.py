"""
job_store.py — Shared job state for the OCR API.

Why this exists
---------------
With multiple Gunicorn workers, in-memory Python variables are NOT shared
between processes. A job created by worker A is invisible to worker B.

This module stores job status in Redis so that:
  * multiple users can run jobs at the same time,
  * a progress bar can read live per-page progress,
  * the frontend can reconnect using only the job_id.

Fail-proof design
-----------------
If Redis is unreachable, we fall back to an in-process dictionary so a single
worker still works. In multi-worker mode Redis is required for cross-worker
visibility, but the app will not crash if Redis is temporarily down.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

LOG = logging.getLogger("ocr.job_store")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

JOB_TTL_SECONDS = int(os.getenv("OCR_JOB_TTL_MINUTES", "60")) * 60

# ---------------------------------------------------------------------------
# Redis client (with graceful fallback)
# ---------------------------------------------------------------------------

_redis_client = None
_memory_store: dict[str, dict] = {}
_memory_lock = threading.Lock()


def _get_redis():
    global _redis_client

    if _redis_client is not None:
        return _redis_client

    try:
        import redis

        client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        client.ping()
        _redis_client = client
        LOG.info("Connected to Redis at %s", REDIS_URL)
        return _redis_client

    except Exception:
        LOG.warning(
            "Redis unavailable; using in-memory job store "
            "(single-worker only)."
        )
        return None


def _key(job_id: str) -> str:
    return f"ocr:job:{job_id}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_job(job_id: str, filename: str = "") -> None:
    mapping = {
        "status": "queued",
        "filename": filename,
        "completed_pages": "0",
        "total_pages": "0",
        "percent": "0",
        "engine": "",
        "created_at": str(time.time()),
    }

    client = _get_redis()

    if client is not None:
        try:
            client.hset(_key(job_id), mapping=mapping)
            client.expire(_key(job_id), JOB_TTL_SECONDS)
            return
        except Exception:
            LOG.exception("Redis create_job failed; using memory.")

    with _memory_lock:
        _memory_store[job_id] = dict(mapping)


def update_progress(
    job_id: str,
    completed: int,
    total: int,
    engine: str = "",
) -> None:
    percent = int(completed / total * 100) if total else 0

    mapping = {
        "status": "processing",
        "completed_pages": str(completed),
        "total_pages": str(total),
        "percent": str(percent),
    }

    if engine:
        mapping["engine"] = engine

    client = _get_redis()

    if client is not None:
        try:
            client.hset(_key(job_id), mapping=mapping)
            client.expire(_key(job_id), JOB_TTL_SECONDS)
            return
        except Exception:
            LOG.exception("Redis update_progress failed; using memory.")

    with _memory_lock:
        if job_id in _memory_store:
            _memory_store[job_id].update(mapping)


def set_result(job_id: str, result: dict) -> None:
    mapping = {
        "status": "completed",
        "percent": "100",
        "result": json.dumps(result, ensure_ascii=False, default=str),
    }

    client = _get_redis()

    if client is not None:
        try:
            client.hset(_key(job_id), mapping=mapping)
            client.expire(_key(job_id), JOB_TTL_SECONDS)
            return
        except Exception:
            LOG.exception("Redis set_result failed; using memory.")

    with _memory_lock:
        if job_id in _memory_store:
            _memory_store[job_id].update(mapping)


def set_error(job_id: str, message: str) -> None:
    mapping = {
        "status": "error",
        "error": message,
    }

    client = _get_redis()

    if client is not None:
        try:
            client.hset(_key(job_id), mapping=mapping)
            client.expire(_key(job_id), JOB_TTL_SECONDS)
            return
        except Exception:
            LOG.exception("Redis set_error failed; using memory.")

    with _memory_lock:
        if job_id in _memory_store:
            _memory_store[job_id].update(mapping)


def get_job(job_id: str) -> Optional[dict]:
    client = _get_redis()

    if client is not None:
        try:
            data = client.hgetall(_key(job_id))
            return data or None
        except Exception:
            LOG.exception("Redis get_job failed; using memory.")

    with _memory_lock:
        job = _memory_store.get(job_id)
        return dict(job) if job else None
