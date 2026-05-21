"""Deduplicator for broadcast operations (Issue #33).

Coalesces rapid, repeated broadcast requests into a single call after a
configurable cooldown.  Uses ``threading.Timer`` internally — if a new
request arrives while a timer is pending the timer is reset so only one
broadcast fires after the burst subsides.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)


class BroadcastDeduplicator:
    """Collapse many broadcast requests into one per cooldown window.

    Usage::

        _broadcast_dedup = BroadcastDeduplicator()

        # Instead of:  threading.Thread(target=fn).start()
        _broadcast_dedup.request_broadcast(fn)
    """

    def __init__(self) -> None:
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def request_broadcast(
        self,
        broadcast_fn: Callable[[], None],
        cooldown_seconds: float = 2.0,
    ) -> None:
        """Schedule *broadcast_fn* to run once after *cooldown_seconds*.

        Subsequent calls within the window reset the timer so only the
        last invocation actually fires.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()

            def _run() -> None:
                with self._lock:
                    self._timer = None
                try:
                    broadcast_fn()
                except Exception:
                    logger.exception("Deduplicated broadcast failed")

            self._timer = threading.Timer(cooldown_seconds, _run)
            self._timer.daemon = True
            self._timer.start()

    # ------------------------------------------------------------------
    def cancel(self) -> None:
        """Cancel any pending broadcast."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


# Module-level singleton shared across the application.
broadcast_dedup = BroadcastDeduplicator()
