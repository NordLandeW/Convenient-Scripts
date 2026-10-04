"""Single-instance file queue with an explicitly owned listener process.

The queue-file lock serializes writers, listener transfers and batch drains.
It also protects the final idle check and instance-lock handoff: a sender either
queues to a live owner or acquires ownership itself.
"""

import multiprocessing
import os

from filelock import FileLock, Timeout


def _read_disk_queue(queue_path):
    """Called only with the queue-file lock held."""
    if not os.path.exists(queue_path):
        return []
    with open(queue_path, "r", encoding="utf-8") as file:
        lines = file.readlines()
    if lines:
        os.remove(queue_path)
    return [line.strip() for line in lines]


def _append_disk_queue(queue_path, file_paths):
    if file_paths:
        with open(queue_path, "a", encoding="utf-8") as file:
            for path in file_paths:
                file.write(path + "\n")


def _queue_listener(queue_path, lock_path, pending, stop_event):
    # Module-level target keeps Windows spawn from serializing the owner and
    # its Manager/Process objects. No CLI or password-sync work runs here.
    while not stop_event.is_set():
        try:
            with FileLock(lock_path).acquire(timeout=0):
                if stop_event.is_set():
                    break
                pending.extend(_read_disk_queue(queue_path))
        except Timeout:
            # A sender or the parent is using the queue; retry on the next pass.
            pass
        stop_event.wait(0.1)


class InstanceQueue:
    def __init__(self, config_dir):
        self.queue_path = os.path.join(config_dir, "queue_file.txt")
        self.queue_lock_path = os.path.join(config_dir, "queue_file.lock")
        self.instance_lock = FileLock(os.path.join(config_dir, "instance.lock"))
        self._owns_instance = False
        self._manager = None
        self._pending = None
        self._process = None
        self._stop_event = None

    def claim_or_submit(self, file_paths):
        """Return True for the primary; otherwise append paths to its queue."""
        with FileLock(self.queue_lock_path):
            try:
                self.instance_lock.acquire(timeout=0)
            except Timeout:
                _append_disk_queue(self.queue_path, file_paths)
                return False
            self._owns_instance = True
            return True

    def start(self):
        if not self._owns_instance:
            raise RuntimeError("Only the primary instance may start a queue listener")
        if self._process is not None:
            return
        context = multiprocessing.get_context()
        self._manager = context.Manager()
        self._pending = self._manager.list()
        self._stop_event = context.Event()
        self._process = context.Process(
            target=_queue_listener,
            args=(self.queue_path, self.queue_lock_path, self._pending, self._stop_event),
            daemon=True,
        )
        try:
            self._process.start()
        except BaseException:
            self.close()
            raise

    def _drain_unlocked(self):
        pending = []
        if self._pending is not None:
            pending = list(self._pending)
            self._pending[:] = []
        pending.extend(_read_disk_queue(self.queue_path))
        return pending

    def next_batch(self):
        """Atomically drain pending paths, or relinquish an idle primary."""
        if not self._owns_instance:
            return []
        with FileLock(self.queue_lock_path):
            pending = self._drain_unlocked()
            if not pending:
                # Signal while holding the same lock used by the listener. A
                # listener waiting on this lock cannot steal the next owner's work.
                if self._stop_event is not None:
                    self._stop_event.set()
                self.instance_lock.release()
                self._owns_instance = False
            return pending

    def close(self):
        """Stop workers, preserve unprocessed queued paths and release ownership."""
        try:
            if self._owns_instance:
                with FileLock(self.queue_lock_path):
                    if self._stop_event is not None:
                        self._stop_event.set()
                    try:
                        pending = self._drain_unlocked()
                        _append_disk_queue(self.queue_path, pending)
                    finally:
                        self.instance_lock.release()
                        self._owns_instance = False
        finally:
            if self._stop_event is not None:
                self._stop_event.set()
            try:
                if self._process is not None and self._process.pid is not None:
                    self._process.join(timeout=2)
                    if self._process.is_alive():
                        self._process.terminate()
                        self._process.join(timeout=5)
                    if self._process.is_alive():
                        raise RuntimeError("The queue listener did not stop")
            finally:
                if self._manager is not None:
                    self._manager.shutdown()
                    self._manager = None
                self._pending = None
                self._process = None
                self._stop_event = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
