"""Application-specific concurrency primitives."""

from __future__ import annotations

import threading
import weakref
from concurrent.futures import ThreadPoolExecutor

try:
    from concurrent.futures.thread import _worker as _threadpool_worker
except ImportError:  # pragma: no cover - depends on Python internals
    _threadpool_worker = None


class AppThreadPoolExecutor(ThreadPoolExecutor):
    """Thread pool whose workers cannot keep the desktop app alive on exit."""

    def _adjust_thread_count(self):  # pragma: no cover - stdlib compatibility shim
        try:
            if self._idle_semaphore.acquire(timeout=0):
                return
            if _threadpool_worker is None:
                return super()._adjust_thread_count()

            def weakref_cb(_, q=self._work_queue):
                q.put(None)

            num_threads = len(self._threads)
            if num_threads < self._max_workers:
                name = f"{self._thread_name_prefix or self}_{num_threads}"
                thread = threading.Thread(
                    name=name,
                    target=_threadpool_worker,
                    args=(
                        weakref.ref(self, weakref_cb),
                        self._work_queue,
                        self._initializer,
                        self._initargs,
                    ),
                    daemon=True,
                )
                thread.start()
                self._threads.add(thread)
        except Exception:
            try:
                return super()._adjust_thread_count()
            except Exception:
                return None
