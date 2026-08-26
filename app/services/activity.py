"""Live activity heartbeats so long pipeline steps do not look frozen."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional


def format_elapsed(seconds: float) -> str:
    sec = max(0, int(seconds))
    if sec < 60:
        return f"{sec}s"
    minutes, rem = divmod(sec, 60)
    if rem:
        return f"{minutes} phút {rem:02d}s"
    return f"{minutes} phút"


@contextmanager
def heartbeat(
    emit: Callable[[str], None],
    label: str,
    interval: float = 8.0,
) -> Iterator[None]:
    """Emit a keep-alive log every `interval` seconds until the block returns."""
    stop = threading.Event()
    started = time.monotonic()

    def _run() -> None:
        while not stop.wait(max(0.05, float(interval))):
            elapsed = time.monotonic() - started
            try:
                emit(
                    f"⏳ {label} — vẫn đang chạy ({format_elapsed(elapsed)}), máy không bị đứng."
                )
            except BaseException:
                return

    thread = threading.Thread(target=_run, name="reup-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)


def emit_status(callback: Optional[Callable[[str], None]], message: str) -> None:
    if not callable(callback):
        return
    try:
        callback(message)
    except Exception:
        return
