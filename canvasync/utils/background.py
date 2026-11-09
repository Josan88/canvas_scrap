"""Run PDF extraction and video transcription while the sync carries on.

Each kind of job gets one worker thread with its own first-in, first-out queue:

- ``PDF``: LibreOffice and the Java extractor run as subprocesses, and docling
  runs in the hybrid server process (on the GPU when there is one). The server
  converts one file at a time, so a second PDF worker would only wait on it.
- ``TRANSCRIBE``: faster-whisper already uses one thread per CPU core, so a
  second transcription worker would only split those cores.

The two kinds mostly use different hardware, so they overlap with each other
and with the Canvas downloads. Jobs of one kind run in the order they were
submitted, so a follow-up job (copying a transcript to a second folder) waits
for the transcription it depends on simply by being queued after it.

Until ``start()`` is called, ``submit()`` runs each job straight away on the
calling thread, as the sync did before.
"""

import threading
import traceback
from queue import Queue
from typing import Callable, Dict, Hashable, Optional, Set

PDF = "pdf"
TRANSCRIBE = "transcribe"
KINDS = (PDF, TRANSCRIBE)
_LABELS = {PDF: "PDF extraction", TRANSCRIBE: "transcription"}


class BackgroundJobs:
    def __init__(self) -> None:
        self._queues: Dict[str, Queue] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._pending = {kind: 0 for kind in KINDS}
        self._keys: Set[Hashable] = set()
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)

    @property
    def running(self) -> bool:
        return bool(self._threads)

    def start(self) -> None:
        """Start one worker thread per kind. Later ``submit()`` calls return at once."""
        if self._threads:
            return
        for kind in KINDS:
            jobs: Queue = Queue()
            thread = threading.Thread(
                target=self._work, args=(kind, jobs), name=f"canvasync-{kind}", daemon=True
            )
            self._queues[kind] = jobs
            self._threads[kind] = thread
            thread.start()

    def submit(self, kind: str, job: Callable[[], None], key: Optional[Hashable] = None) -> bool:
        """Queue *job* (no arguments) to run on *kind*'s worker.

        A job whose *key* was already submitted in this run is dropped, so the
        same file is never extracted or transcribed twice. Returns True when the
        job was accepted.
        """
        if kind not in KINDS:
            raise ValueError(f"unknown job kind: {kind}")
        if not self._threads:
            job()
            return True
        with self._lock:
            if key is not None:
                if key in self._keys:
                    return False
                self._keys.add(key)
            self._pending[kind] += 1
        self._queues[kind].put(job)
        return True

    def pending(self) -> Dict[str, int]:
        """Jobs submitted but not yet finished, per kind."""
        with self._lock:
            return dict(self._pending)

    def describe_pending(self) -> str:
        counts = self.pending()
        return ", ".join(f"{n} {_LABELS[kind]}(s)" for kind, n in counts.items() if n)

    def wait(self, poll_seconds: float = 0.5) -> None:
        """Block until every submitted job has finished.

        Waits in short slices so Ctrl+C still interrupts on Windows.
        """
        with self._lock:
            while any(self._pending.values()):
                self._changed.wait(timeout=poll_seconds)

    def stop(self) -> None:
        """Finish the queued jobs, then stop the workers (later jobs run inline again)."""
        if not self._threads:
            return
        self.wait()
        for jobs in self._queues.values():
            jobs.put(None)
        for thread in self._threads.values():
            thread.join()
        self._queues.clear()
        self._threads.clear()
        with self._lock:
            self._keys.clear()

    def _work(self, kind: str, jobs: Queue) -> None:
        while True:
            job = jobs.get()
            if job is None:
                return
            try:
                job()
            except Exception:
                print(f"Background {_LABELS[kind]} job failed:")
                traceback.print_exc()
            finally:
                with self._lock:
                    self._pending[kind] -= 1
                    self._changed.notify_all()


background_jobs = BackgroundJobs()
