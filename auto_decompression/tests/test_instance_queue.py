"""Queue delivery and lifecycle checks with isolated files and bounded workers."""

import multiprocessing
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from filelock import Timeout

import instance_queue
from instance_queue import InstanceQueue


def _submit_in_child(config_dir, paths, connection):
    try:
        with InstanceQueue(config_dir) as queue:
            connection.send(("role", queue.claim_or_submit(paths)))
    except BaseException as error:
        connection.send(("error", repr(error)))
    finally:
        connection.close()


class InstanceQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="autodec-queue-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.owners = []
        self.addCleanup(self.close_owners)

    def close_owners(self):
        for owner in reversed(self.owners):
            owner.close()

    def owner(self):
        owner = InstanceQueue(str(self.root))
        self.owners.append(owner)
        return owner

    def test_secondary_delivery_preserves_order_and_unicode(self):
        primary = self.owner()
        self.assertTrue(primary.claim_or_submit([]))
        secondary = self.owner()
        self.assertFalse(secondary.claim_or_submit(["first.zip", "第二个.rar"]))
        secondary.close()
        self.assertEqual(primary.next_batch(), ["first.zip", "第二个.rar"])
        self.assertEqual(primary.next_batch(), [])
        self.assertTrue(self.owner().claim_or_submit([]))

    def test_non_releasing_empty_drain_keeps_primary_until_final_check(self):
        primary = self.owner()
        self.assertTrue(primary.claim_or_submit([]))
        self.assertEqual(primary.next_batch(release_if_idle=False), [])
        self.assertFalse(self.owner().claim_or_submit(["during-sync.zip"]))
        self.assertEqual(primary.next_batch(), ["during-sync.zip"])
        self.assertFalse(self.owner().claim_or_submit(["while-processing.zip"]))
        self.assertEqual(primary.next_batch(release_if_idle=False), ["while-processing.zip"])
        self.assertEqual(primary.next_batch(release_if_idle=False), [])
        self.assertFalse(self.owner().claim_or_submit([]))
        self.assertEqual(primary.next_batch(), [])
        self.assertTrue(self.owner().claim_or_submit([]))

    def test_non_releasing_drain_leaves_listener_available_for_late_work(self):
        primary = self.owner()
        self.assertTrue(primary.claim_or_submit([]))
        primary.start()
        self.assertEqual(primary.next_batch(release_if_idle=False), [])
        self.assertFalse(self.owner().claim_or_submit(["late.zip"]))
        self.assertEqual(primary.next_batch(release_if_idle=False), ["late.zip"])
        self.assertEqual(primary.next_batch(), [])
        replacement = self.owner()
        self.assertTrue(replacement.claim_or_submit([]))
        self.assertFalse(self.owner().claim_or_submit(["replacement.zip"]))
        primary.close()
        self.assertEqual(replacement.next_batch(), ["replacement.zip"])

    def test_listener_start_failure_keeps_ownership_until_outer_cleanup(self):
        primary = self.owner()
        self.assertTrue(primary.claim_or_submit([]))
        before = {child.pid for child in multiprocessing.active_children()}
        # Fail only the listener start, after the real Manager has been created.
        original_process = instance_queue.multiprocessing.get_context().Process

        def failing_listener(*args, **kwargs):
            process = original_process(*args, **kwargs)
            if kwargs.get("target") is instance_queue._queue_listener:
                process.start = unittest.mock.Mock(side_effect=OSError("listener fixture failure"))
            return process

        context = instance_queue.multiprocessing.get_context()
        with patch.object(context, "Process", side_effect=failing_listener):
            with self.assertRaisesRegex(OSError, "listener fixture failure"):
                primary.start()
        self.assertFalse(self.owner().claim_or_submit(["preserved-after-start-failure.zip"]))
        primary.close()
        self.assertEqual({child.pid for child in multiprocessing.active_children()}, before)
        replacement = self.owner()
        self.assertTrue(replacement.claim_or_submit([]))
        self.assertEqual(replacement.next_batch(), ["preserved-after-start-failure.zip"])

    def test_listener_and_manager_are_stopped_after_drain(self):
        before = {child.pid for child in multiprocessing.active_children()}
        primary = self.owner()
        self.assertTrue(primary.claim_or_submit([]))
        primary.start()
        secondary = self.owner()
        self.assertFalse(secondary.claim_or_submit(["one.zip", "two.zip"]))
        self.assertEqual(primary.next_batch(), ["one.zip", "two.zip"])
        self.assertEqual(primary.next_batch(), [])
        primary.close()
        self.assertEqual({child.pid for child in multiprocessing.active_children()}, before)

    def test_close_preserves_unprocessed_paths_for_next_primary(self):
        primary = self.owner()
        self.assertTrue(primary.claim_or_submit([]))
        primary.start()
        self.assertFalse(self.owner().claim_or_submit(["pending.zip"]))
        primary.close()
        replacement = self.owner()
        self.assertTrue(replacement.claim_or_submit([]))
        self.assertEqual(replacement.next_batch(), ["pending.zip"])
        self.assertEqual(replacement.next_batch(), [])

    def test_idle_handoff_does_not_let_old_listener_steal_new_work(self):
        first = self.owner()
        self.assertTrue(first.claim_or_submit([]))
        first.start()
        self.assertEqual(first.next_batch(), [])
        second = self.owner()
        self.assertTrue(second.claim_or_submit([]))
        second.start()
        self.assertFalse(self.owner().claim_or_submit(["new-owner.zip"]))
        first.close()
        self.assertEqual(second.next_batch(), ["new-owner.zip"])
        self.assertEqual(second.next_batch(), [])

    def test_real_secondary_process_delivers_without_becoming_primary(self):
        primary = self.owner()
        self.assertTrue(primary.claim_or_submit([]))
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_submit_in_child, args=(str(self.root), ["from-child.zip"], send))
        process.start()
        send.close()
        try:
            self.assertTrue(receive.poll(15), "secondary process did not complete its submission")
            self.assertEqual(receive.recv(), ("role", False))
            process.join(timeout=15)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(primary.next_batch(), ["from-child.zip"])
        finally:
            receive.close()
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    def test_listener_retries_lock_contention_without_losing_paths(self):
        queue_file = self.root / "queue_file.txt"
        queue_file.write_text("after-contention.zip\n", encoding="utf-8")
        stopped = threading.Event()
        pending = []
        lock_attempts = []

        class ScheduledLock:
            def acquire(self, timeout):
                lock_attempts.append(timeout)
                if len(lock_attempts) == 1:
                    raise Timeout("fixture-lock")
                return self

            def __enter__(self):
                return self

            def __exit__(self, *args):
                stopped.set()

        class ControlledEvent:
            def is_set(self):
                return stopped.is_set()

            def wait(self, timeout):
                # The injected lock schedule, not wall-clock timing, determines
                # the first contention and following successful transfer.
                return stopped.is_set()

        with patch.object(instance_queue, "FileLock", return_value=ScheduledLock()):
            instance_queue._queue_listener(str(queue_file), "fixture-lock", pending, ControlledEvent())
        self.assertEqual(pending, ["after-contention.zip"])
        self.assertEqual(len(lock_attempts), 2)
        self.assertFalse(queue_file.exists())

    def test_stopped_listener_leaves_next_owners_queue_untouched(self):
        path = self.root / "queue_file.txt"
        path.write_text("belongs-to-next-owner.zip\n", encoding="utf-8")
        event = threading.Event()
        event.set()
        pending = []
        instance_queue._queue_listener(str(path), str(self.root / "queue_file.lock"), pending, event)
        self.assertEqual(pending, [])
        self.assertEqual(path.read_text(encoding="utf-8"), "belongs-to-next-owner.zip\n")


if __name__ == "__main__":
    unittest.main()
