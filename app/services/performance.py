"""Shared resource limits for native CPU/GPU processing stages."""

import threading
from contextlib import contextmanager

from app.config import settings


_GPU_SLOTS = threading.BoundedSemaphore(max(1, int(settings.GPU_CONCURRENCY)))


@contextmanager
def gpu_task_slot(enabled: bool = True):
    """Serialize VRAM-heavy stages on low-memory GPUs without blocking CPU work."""
    if not enabled:
        yield
        return
    with _GPU_SLOTS:
        yield
