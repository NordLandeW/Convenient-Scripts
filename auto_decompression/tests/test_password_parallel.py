"""Bounded password probes must never concurrently extract into shared output."""

from contextlib import redirect_stdout
import io
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

import extraction


class PasswordWorkerCounts(unittest.TestCase):
    def test_default_is_eighty_percent_of_logical_cpus_rounded_down(self):
        for cpus, expected in [(None, 1), (1, 1), (2, 1), (8, 6), (16, 12), (32, 25)]:
            with self.subTest(cpus=cpus), patch.object(extraction.os, "cpu_count", return_value=cpus):
                self.assertEqual(extraction.get_password_workers(), expected)

    def test_explicit_worker_count_and_invalid_values(self):
        self.assertEqual(extraction.get_password_workers(3), 3)
        for workers in (0, -1, 1.5, "3"):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                extraction.get_password_workers(workers)


class ReadOnlyPasswordProbes(unittest.TestCase):
    def setUp(self):
        self.stop = threading.Event()
        self.process = Mock()
        self.process.wait.return_value = 0
        self.process.poll.return_value = 0
        self.launch = self.enterContext(patch.object(
            extraction.subprocess, "Popen", return_value=self.process
        ))

    def test_7zip_uses_read_only_test_and_one_internal_thread(self):
        self.assertTrue(extraction._test_archive_password("input.7z", "密码", self.stop))
        command = self.launch.call_args.args[0]
        self.assertEqual(command[:3], ["7z", "t", "input.7z"])
        self.assertIn("-p密码", command)
        self.assertIn("-mmt=1", command)
        self.assertFalse(any(arg.startswith("-o") for arg in command))
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(self.launch.call_args.kwargs[stream], subprocess.DEVNULL)
        self.process.terminate.assert_not_called()

    def test_bandizip_uses_read_only_test_and_password_switch(self):
        self.assertTrue(extraction._test_archive_password("input.zip", "secret", self.stop, backend="bz"))
        command = self.launch.call_args.args[0]
        self.assertEqual(command[:2], ["bz", "t"])
        self.assertIn("-p:secret", command)
        self.assertIn("input.zip", command)
        self.assertFalse(any(arg.startswith("-o") for arg in command))

    def test_nonzero_exit_is_not_a_matching_password(self):
        self.process.wait.return_value = 2
        self.process.poll.return_value = 2
        self.assertFalse(extraction._test_archive_password("input.zip", "bad", self.stop))

    def test_already_cancelled_probe_does_not_launch_a_process(self):
        self.stop.set()
        self.assertFalse(extraction._test_archive_password("input.zip", "unused", self.stop))
        self.launch.assert_not_called()

    def test_cancellation_terminates_and_reaps_running_process(self):
        def wait(timeout):
            if not self.stop.is_set():
                self.stop.set()
                raise subprocess.TimeoutExpired("7z", timeout)
            return -1
        self.process.wait.side_effect = wait
        self.process.poll.return_value = None
        self.assertFalse(extraction._test_archive_password("input.zip", "slow", self.stop))
        self.process.terminate.assert_called_once()
        self.assertEqual(self.process.wait.call_count, 2)

    def test_cancellation_kills_process_if_termination_times_out(self):
        def wait(timeout=None):
            if timeout is None:
                return -1
            self.stop.set()
            raise subprocess.TimeoutExpired("7z", timeout)
        self.process.wait.side_effect = wait
        self.process.poll.return_value = None
        self.assertFalse(extraction._test_archive_password("input.zip", "slow", self.stop))
        self.process.terminate.assert_called_once()
        self.process.kill.assert_called_once()
        self.assertEqual(self.process.wait.call_count, 3)

    def test_wait_error_still_terminates_and_reaps_running_process(self):
        self.process.wait.side_effect = [OSError("wait failed"), -1]
        self.process.poll.return_value = None
        with self.assertRaisesRegex(OSError, "wait failed"):
            extraction._test_archive_password("input.zip", "key", self.stop)
        self.process.terminate.assert_called_once()
        self.assertEqual(self.process.wait.call_count, 2)

    def test_launch_error_is_not_reported_as_success(self):
        self.launch.side_effect = FileNotFoundError("7z not found")
        with self.assertRaises(FileNotFoundError):
            extraction._test_archive_password("input.zip", "key", self.stop)


class ParallelPasswordDictionary(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))
        self.extract = self.enterContext(patch.object(extraction, "extract_with_7zip", return_value=1))
        self.probe = self.enterContext(patch.object(extraction, "_test_archive_password", return_value=False))

    def test_single_worker_preserves_frequency_order_skips_last_and_duplicates(self):
        self.probe.side_effect = lambda filename, password, stop, **kwargs: password == "last"
        result = extraction.try_passwords(
            "input", "output", [("most", 9), ("middle", 5), ("most", 2), ("last", 1)],
            "middle", password_workers=1,
        )
        self.assertEqual(result, "last")
        self.assertEqual([call.args[1] for call in self.probe.call_args_list], ["most", "last"])
        self.extract.assert_called_once_with("input", "output", "last")

    def test_empty_and_exhausted_dictionaries_do_not_extract(self):
        for passwords in ([], [("already", 1)]):
            self.assertIsNone(extraction.try_passwords("input", "output", passwords, "already"))
        self.probe.assert_not_called()
        self.extract.assert_not_called()

    def test_all_wrong_passwords_are_tested_without_extracting(self):
        passwords = [(str(number), number) for number in range(20)]
        self.assertIsNone(extraction.try_passwords("input", "output", passwords, None, password_workers=3))
        self.assertCountEqual([call.args[1] for call in self.probe.call_args_list], [str(n) for n in range(20)])
        self.extract.assert_not_called()

    def test_default_worker_count_is_used_and_capped_by_candidate_count(self):
        real_executor = extraction.ThreadPoolExecutor
        with patch.object(extraction.os, "cpu_count", return_value=8), patch.object(
            extraction, "ThreadPoolExecutor", wraps=real_executor
        ) as executor:
            extraction.try_passwords("input", "output", [(str(n), n) for n in range(20)], None)
            self.assertEqual(executor.call_args.kwargs["max_workers"], 6)
            extraction.try_passwords("input", "output", [("only", 1)], None)
            self.assertEqual(executor.call_args.kwargs["max_workers"], 1)

    def test_probes_really_overlap_but_extraction_runs_once_on_caller_thread(self):
        barrier = threading.Barrier(3)
        thread_ids = set()
        lock = threading.Lock()
        def probe(filename, password, stop, **kwargs):
            with lock:
                thread_ids.add(threading.get_ident())
            barrier.wait(timeout=5)
            return password == "good"
        self.probe.side_effect = probe
        caller = threading.get_ident()
        extraction_threads = []
        self.extract.side_effect = lambda *args: extraction_threads.append(threading.get_ident()) or 1
        result = extraction.try_passwords(
            "input", "output", [("bad1", 3), ("good", 2), ("bad2", 1)], None,
            password_workers=3,
        )
        self.assertEqual(result, "good")
        self.assertEqual(len(thread_ids), 3)
        self.assertNotIn(caller, thread_ids)
        self.assertEqual(extraction_threads, [caller])

    def test_winner_stops_running_probes_and_does_not_submit_remaining_dictionary(self):
        slow_started = threading.Event()
        slow_finished = threading.Event()
        def probe(filename, password, stop, **kwargs):
            if password == "slow":
                slow_started.set()
                if not stop.wait(timeout=5):
                    raise AssertionError("Running probe was not cancelled")
                slow_finished.set()
                return False
            self.assertTrue(slow_started.wait(timeout=5))
            return True
        self.probe.side_effect = probe
        def extract(*args):
            self.assertTrue(slow_finished.is_set(), "All probes must stop before extraction")
            return 1
        self.extract.side_effect = extract
        passwords = [("slow", 100), ("good", 99)] + [(str(n), n) for n in range(50)]
        self.assertEqual(extraction.try_passwords("input", "output", passwords, None, password_workers=2), "good")
        self.assertCountEqual([call.args[1] for call in self.probe.call_args_list], ["slow", "good"])
        self.extract.assert_called_once()

    def test_successful_probe_does_not_hide_final_extraction_failure(self):
        self.probe.return_value = True
        self.extract.return_value = -3
        self.assertIsNone(extraction.try_passwords("input", "output", [("good", 1)], None))
        self.extract.assert_called_once()

    def test_probe_exception_cancels_other_workers_and_propagates(self):
        slow_started = threading.Event()
        stopped = threading.Event()
        def probe(filename, password, stop, **kwargs):
            if password == "slow":
                slow_started.set()
                if not stop.wait(timeout=5):
                    raise AssertionError("Failed pool left a running probe")
                stopped.set()
                return False
            self.assertTrue(slow_started.wait(timeout=5))
            raise OSError("probe failure")
        self.probe.side_effect = probe
        with self.assertRaisesRegex(OSError, "probe failure"):
            extraction.try_passwords("input", "output", [("slow", 2), ("error", 1)], None, password_workers=2)
        self.assertTrue(stopped.is_set())
        self.extract.assert_not_called()

    def test_bandizip_dictionary_uses_parallel_read_only_probes_then_one_extraction(self):
        self.probe.side_effect = lambda filename, password, stop, **kwargs: password == "good"
        with patch.object(extraction, "extract_with_bandizip", return_value=1) as backend:
            result = extraction.handle_bandizip_extraction(
                "input", "output", [("bad", 2), ("good", 1)], 1, password_workers=2
            )
        self.assertEqual(result, "good")
        self.assertTrue(all(call.kwargs["backend"] == "bz" for call in self.probe.call_args_list))
        backend.assert_called_once_with("input", "output", "good")
        self.extract.assert_not_called()

    def test_bandizip_exhaustion_retains_manual_entry_and_skip(self):
        with patch("builtins.input", side_effect=["manual", ""]), patch.object(
            extraction, "extract_with_bandizip", return_value=-1
        ) as backend:
            self.assertIsNone(extraction.handle_bandizip_extraction(
                "input", "output", [("bad", 1)], 2, password_workers=1
            ))
        backend.assert_called_once_with("input", "output", "manual")


if __name__ == "__main__":
    unittest.main()
