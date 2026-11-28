"""Bộ lắng nghe sự kiện và lấy mẫu vận hành theo từng tiến trình API."""
import json
import logging
import os
import time
import tracemalloc
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import anyio
from anyio import to_thread

from app import config
from app.services.domain_events import DomainEvent

logger = logging.getLogger("uvicorn.error.concert")


def log_domain_event(event: DomainEvent) -> None:
    fields = {
        "event": event.name,
        "timestamp": event.timestamp.replace(tzinfo=timezone.utc).isoformat(),
        "pid": os.getpid(),
        "order_id": event.order_id,
        "quantity": event.quantity,
    }
    if event.webhook_to_qr_ms is not None:
        fields["webhook_to_qr_ms"] = round(event.webhook_to_qr_ms, 3)
    logger.info("domain_event %s", json.dumps(fields))


async def _sample_metrics(database_metrics, interval: float) -> None:
    http_limiter = to_thread.current_default_thread_limiter()
    metrics_limiter = anyio.CapacityLimiter(1)
    while True:
        await anyio.sleep(interval)
        timestamp = datetime.now(timezone.utc).isoformat()
        stats = http_limiter.statistics()
        started = time.monotonic()
        with anyio.move_on_after(interval) as probe:
            dispatched = await to_thread.run_sync(time.monotonic, abandon_on_cancel=True)
        waited = (time.monotonic() if probe.cancelled_caught else dispatched) - started
        fields = {
            "timestamp": timestamp,
            "pid": os.getpid(),
            "threadpool_in_use": stats.borrowed_tokens,
            "threadpool_capacity": stats.total_tokens,
            "threadpool_waiters": stats.tasks_waiting,
            "threadpool_wait_ms": round(waited * 1000, 3),
            "threadpool_probe_timed_out": probe.cancelled_caught,
        }
        try:
            fields.update(await to_thread.run_sync(
                database_metrics, abandon_on_cancel=True, limiter=metrics_limiter,
            ))
        except Exception:
            logger.exception("Lỗi lấy mẫu chỉ số DB")
        if tracemalloc.is_tracing():
            fields["python_traced_bytes"], fields["python_peak_traced_bytes"] = tracemalloc.get_traced_memory()
        logger.info("ops_metrics %s", json.dumps(fields))


@asynccontextmanager
async def observe_runtime(database_metrics):
    frames = config.OBSERVABILITY_TRACEMALLOC_FRAMES
    owns_trace = frames > 0 and not tracemalloc.is_tracing()
    if owns_trace:
        tracemalloc.start(frames)
    try:
        async with anyio.create_task_group() as group:
            interval = config.OBSERVABILITY_INTERVAL_SECONDS
            if interval > 0:
                group.start_soon(_sample_metrics, database_metrics, interval)
            try:
                yield
            finally:
                group.cancel_scope.cancel()
    finally:
        if owns_trace:
            tracemalloc.stop()
