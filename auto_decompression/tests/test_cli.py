"""CLI orchestration with isolated paths and explicit service substitutes."""

from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import multiprocessing
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from filelock import FileLock, Timeout

import auto_decompression as cli


def _run_isolated_cli(config_dir, data_dir, files, connection, fail_processing=False):
    """Run real queue/book ownership with a parent-controlled Gist substitute."""
    class ReportingLock:
        def __init__(self, *args, **kwargs):
            self.lock = FileLock(*args, **kwargs)

        def __enter__(self):
            try:
                self.lock.acquire(timeout=0)
            except Timeout:
                # A real failed acquisition is the ordering witness, not a delay.
                connection.send(("waiting_for_book",))
                self.lock.acquire()
            return self

        def __exit__(self, *args):
            self.lock.release()

    class FixtureBook(cli.PasswordBook):
        def __init__(self, book_config, book_data, script_dir):
            # Even legacy migration is restricted to this temporary fixture.
            super().__init__(book_config, book_data, book_config)
            connection.send(("constructed", Path(book_config).name))

        def check_passwords(self):
            result = super().check_passwords()
            connection.send(("loaded", result, dict(self.passwords)))
            return result

        def _fetch_from_gist(self, cfg):
            return {}, None

        def _update_gist(self, cfg, content):
            connection.send(("upload", cfg["gist_id"], json.loads(content)))
            if not connection.poll(30):
                raise RuntimeError("fixture upload was not released")
            action = connection.recv()
            if action == "fail":
                raise RuntimeError("fixture upload failure")
            if action != "finish":
                raise AssertionError(f"unknown fixture upload action: {action!r}")
            return True

    class FixtureWorkflow:
        def __init__(self, book, **options):
            self.book = book
            self.embedded_scan_depth = options["embedded_scan_depth"]

        def process_files(self, paths):
            for path in paths:
                self.book.add_password(path)
            self.book.save_passwords()
            connection.send(("processed", list(paths)))
            if fail_processing:
                raise OSError("fixture extraction failure")

    try:
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(io.StringIO()))
            stack.enter_context(patch.object(cli, "PlatformDirs", return_value=SimpleNamespace(
                user_config_dir=config_dir, user_data_dir=data_dir
            )))
            stack.enter_context(patch.object(cli, "FileLock", ReportingLock))
            stack.enter_context(patch.object(cli, "PasswordBook", FixtureBook))
            stack.enter_context(patch.object(cli, "ExtractionWorkflow", FixtureWorkflow))
            # Replace only application delays; filelock retains its real waiting.
            stack.enter_context(patch.object(cli, "time", SimpleNamespace(sleep=lambda seconds: None)))
            stack.enter_context(patch("passwords.time", SimpleNamespace(sleep=lambda seconds: None)))
            stack.enter_context(patch("builtins.input", side_effect=["manual-password", ""] if not files else []))
            stack.enter_context(patch("requests.sessions.Session.request", side_effect=AssertionError("unexpected network request")))
            stack.enter_context(patch.object(cli, "error_end", side_effect=lambda error: connection.send(
                ("reported_error", type(error).__name__, str(error))
            )))
            result = cli.main(["--config-dir", config_dir, *files])
            children = multiprocessing.active_children()
            if children:
                raise AssertionError(f"CLI left workers running: {[child.pid for child in children]}")
            connection.send(("result", result))
    except BaseException as error:
        connection.send(("worker_error", repr(error)))
    finally:
        connection.close()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))
        self.temp = tempfile.TemporaryDirectory(prefix="autodec-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directories = SimpleNamespace(user_config_dir=str(self.root / "config"), user_data_dir=str(self.root / "data"))
        self.book = Mock()
        self.book.skip_gist_sync = False
        self.book.check_passwords.return_value = True
        self.queue = Mock()
        self.queue.claim_or_submit.return_value = True
        self.queue.next_batch.return_value = []
        self.runner = Mock(embedded_scan_depth=2)
        for name, kwargs in (
            ("PlatformDirs", {"return_value": self.directories}),
            ("PasswordBook", {"return_value": self.book}),
            ("ExtractionWorkflow", {"return_value": self.runner}),
        ):
            replacement = patch.object(cli, name, **kwargs)
            replacement.start()
            self.addCleanup(replacement.stop)
        context = patch.object(cli, "InstanceQueue")
        self.queue_factory = context.start()
        self.queue_factory.return_value.__enter__.return_value = self.queue
        self.addCleanup(context.stop)
        delay = patch.object(cli.time, "sleep")
        delay.start()
        self.addCleanup(delay.stop)

    def test_cli_defaults_and_boolean_spellings(self):
        args = cli.parse_cli_arguments(["a.zip"])
        self.assertTrue(args.trash_on_success)
        self.assertTrue(args.flatten_single_file)
        self.assertEqual(args.embedded_scan_depth, 2)
        for text in ("false", "F", "0", "no", "n"):
            self.assertFalse(cli.str2bool(text))
        self.assertTrue(cli.str2bool("YES"))

    def test_password_workers_default_and_override(self):
        with patch("extraction.os.cpu_count", return_value=16):
            self.assertEqual(cli.parse_cli_arguments([]).password_workers, 12)
        self.assertEqual(cli.parse_cli_arguments(["--password-workers", "3"]).password_workers, 3)
        with patch("sys.stderr", new=io.StringIO()):
            for value in ("0", "-1", "abc", "1.5"):
                with self.subTest(value=value), self.assertRaises(SystemExit) as error:
                    cli.parse_cli_arguments(["--password-workers", value])
                self.assertEqual(error.exception.code, 2)

    def test_password_workers_is_forwarded_to_workflow(self):
        self.assertEqual(cli.main(["--password-workers", "3", "file.zip"]), 0)
        self.assertEqual(cli.ExtractionWorkflow.call_args.kwargs["password_workers"], 3)


    def test_show_paths_does_not_start_queue_or_sync_book(self):
        override = self.root / "override"
        self.assertEqual(cli.main(["--config-dir", str(override), "--show-paths"]), 0)
        self.assertTrue(override.is_dir())
        self.assertTrue(Path(self.directories.user_data_dir).is_dir())
        self.queue_factory.assert_not_called()
        cli.PasswordBook.assert_not_called()

    def test_secondary_only_submits_paths(self):
        self.queue.claim_or_submit.return_value = False
        self.assertEqual(cli.main(["--use-binwalk", "a.zip"]), 0)
        self.queue.claim_or_submit.assert_called_once_with(["a.zip"])
        cli.PasswordBook.assert_not_called()
        self.queue.start.assert_not_called()
        self.runner.process_files.assert_not_called()

    def test_secondary_warns_that_password_worker_override_is_not_applied(self):
        self.queue.claim_or_submit.return_value = False
        with patch.object(cli, "print_warning") as warning:
            self.assertEqual(cli.main(["--password-workers", "999", "a.zip"]), 0)
        self.assertTrue(any("--password-workers" in call.args[0] for call in warning.call_args_list))
        cli.ExtractionWorkflow.assert_not_called()

    def test_primary_processes_batches_before_single_exit_sync(self):
        self.queue.next_batch.side_effect = [["later.rar"], [], []]
        self.assertEqual(cli.main(["--trash-on-success", "false", "first.zip"]), 0)
        self.assertEqual([call.args[0] for call in self.runner.process_files.call_args_list], [["first.zip"], ["later.rar"]])
        self.assertFalse(cli.ExtractionWorkflow.call_args.kwargs["trash_on_success"])
        self.book.ensure_gist_config.assert_called_once()
        self.book.check_passwords.assert_called_once()
        self.book.sync_to_gist_before_exit.assert_called_once()
        self.queue.start.assert_called_once()

    def test_update_dictionary_exits_without_starting_listener(self):
        self.book.pull_from_gist_if_possible.return_value = True
        self.assertEqual(cli.main(["--update-dict"]), 0)
        self.assertTrue(self.book.skip_gist_sync)
        self.book.check_passwords.assert_not_called()
        self.queue.start.assert_not_called()
        self.book.sync_to_gist_before_exit.assert_not_called()

    def test_failed_update_preserves_failure_exit(self):
        self.book.pull_from_gist_if_possible.return_value = False
        self.assertEqual(cli.main(["--update-dict"]), 1)
        self.queue.start.assert_not_called()

    def test_startup_conflict_check_remains_opt_in(self):
        self.assertEqual(cli.main(["--check-dict-conflict-on-startup", "file.zip"]), 0)
        self.book.check_dict_conflict_on_startup.assert_called_once()

    def test_password_entry_keeps_zero_initial_count_and_accepts_queued_files(self):
        self.queue.next_batch.side_effect = [["arrived.zip"], [], []]
        with patch("builtins.input", side_effect=["new-password", ""]):
            self.assertEqual(cli.main([]), 0)
        self.book.add_password.assert_called_once_with("new-password", 0)
        self.book.save_passwords.assert_called_once()
        self.runner.process_files.assert_called_once_with(["arrived.zip"])

    def test_failure_still_finalizes_primary_once(self):
        self.runner.process_files.side_effect = OSError("fixture failure")
        with patch.object(cli, "error_end") as report:
            self.assertEqual(cli.main(["input.zip"]), 1)
        report.assert_called_once()
        self.book.sync_to_gist_before_exit.assert_called_once()
        self.queue_factory.return_value.__exit__.assert_called_once()

    def test_failed_load_never_processes_or_syncs(self):
        self.book.check_passwords.return_value = False
        self.assertEqual(cli.main(["input.zip"]), 1)
        self.runner.process_files.assert_not_called()
        self.queue.start.assert_not_called()
        self.book.sync_to_gist_before_exit.assert_not_called()

    def test_initialization_errors_never_sync_an_unloaded_book(self):
        for stage in ("construction", "configuration", "load"):
            with self.subTest(stage=stage):
                error = OSError(f"fixture {stage} failure")
                target = {
                    "construction": cli.PasswordBook,
                    "configuration": self.book.ensure_gist_config,
                    "load": self.book.check_passwords,
                }[stage]
                target.side_effect = error
                try:
                    with patch.object(cli, "error_end") as report:
                        self.assertEqual(cli.main(["input.zip"]), 1)
                    self.assertIs(report.call_args.args[0], error)
                    self.book.sync_to_gist_before_exit.assert_not_called()
                    self.runner.process_files.assert_not_called()
                finally:
                    target.side_effect = None

    def test_start_failure_syncs_before_owner_cleanup(self):
        error = OSError("fixture listener startup failure")
        self.queue.start.side_effect = error

        ownership_observed = []

        def sync_while_owned():
            queue_context_open = not self.queue_factory.return_value.__exit__.called
            try:
                with FileLock(str(Path(self.directories.user_data_dir) / "passwords.lock")).acquire(timeout=0):
                    book_locked = False
            except Timeout:
                book_locked = True
            ownership_observed.append((queue_context_open, book_locked))

        self.book.sync_to_gist_before_exit.side_effect = sync_while_owned
        with patch.object(cli, "error_end") as report:
            self.assertEqual(cli.main(["input.zip"]), 1)
        self.assertIs(report.call_args.args[0], error)
        self.book.sync_to_gist_before_exit.assert_called_once()
        self.queue_factory.return_value.__exit__.assert_called_once()
        self.assertEqual(ownership_observed, [(True, True)])

    def test_sync_failure_does_not_replace_original_processing_error(self):
        error = OSError("original extraction failure")
        self.runner.process_files.side_effect = error
        self.book.sync_to_gist_before_exit.side_effect = RuntimeError("later sync failure")
        with patch.object(cli, "error_end") as report:
            self.assertEqual(cli.main(["input.zip"]), 1)
        self.assertIs(report.call_args.args[0], error)
        self.book.save_passwords.assert_called_once()
        self.book.sync_to_gist_before_exit.assert_called_once()


class SyncOwnershipTests(unittest.TestCase):
    """Observe real cross-process lock ownership at a blocked upload boundary."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="autodec-sync-ownership-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = multiprocessing.get_context("spawn")

    def fixture(self, config_name, data_name="data"):
        config, data = self.root / config_name, self.root / data_name
        config.mkdir(exist_ok=True)
        data.mkdir(exist_ok=True)
        (config / "gist_config.json").write_text(json.dumps({
            "token": "fixture-token", "gist_id": config_name, "file": "dict.json"
        }), encoding="utf-8")
        if not (data / "dict.json").exists():
            (data / "dict.json").write_text(json.dumps({"seed": 2}), encoding="utf-8")
        return config, data

    def spawn_cli(self, config, data, files, *, fail_processing=False):
        parent, child = self.context.Pipe()
        process = self.context.Process(
            target=_run_isolated_cli,
            args=(str(config), str(data), files, child, fail_processing),
        )
        process.start()
        child.close()

        def cleanup():
            # Closing the pipe releases a worker waiting for an upload command.
            parent.close()
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

        self.addCleanup(cleanup)
        return process, parent

    def expect(self, worker, expected):
        process, connection = worker
        self.assertTrue(connection.poll(30), f"worker {process.pid} did not report {expected!r}")
        self.assertEqual(connection.recv(), expected)

    def finish(self, worker, result=0):
        self.expect(worker, ("result", result))
        worker[0].join(timeout=15)
        self.assertFalse(worker[0].is_alive())
        self.assertEqual(worker[0].exitcode, 0)

    def expect_started(self, worker, config, initial, files):
        self.expect(worker, ("constructed", config.name))
        self.expect(worker, ("loaded", True, initial))
        if files:
            self.expect(worker, ("processed", files))

    def test_upload_accepts_secondary_without_loading_another_book_and_resyncs(self):
        for index, (action, initial_files) in enumerate((("finish", ["first.zip"]), ("fail", ["first.zip"]), ("finish", []))):
            with self.subTest(upload_action=action, initial_files=initial_files):
                config, data = self.fixture(f"config-{index}", f"data-{index}")
                primary = self.spawn_cli(config, data, initial_files)
                self.expect_started(primary, config, {"seed": 2}, initial_files)
                expected = {"seed": 2}
                expected.update({path: 1 for path in initial_files} if initial_files else {"manual-password": 0})
                self.expect(primary, ("upload", config.name, expected))
                secondary = self.spawn_cli(config, data, ["during-upload.zip"])
                # No constructor/load event may precede the secondary result.
                self.finish(secondary)
                primary[1].send(action)
                self.expect(primary, ("processed", ["during-upload.zip"]))
                expected["during-upload.zip"] = 1
                self.expect(primary, ("upload", config.name, expected))
                primary[1].send("finish")
                self.finish(primary)
                self.assertEqual(json.loads((data / "dict.json").read_text(encoding="utf-8")), expected)
                with cli.InstanceQueue(str(config)) as successor:
                    self.assertTrue(successor.claim_or_submit([]))
                    self.assertEqual(successor.next_batch(), [])

    def test_processing_failure_sync_holds_owner_and_preserves_accepted_work(self):
        config, data = self.fixture("failure-config")
        failed = self.spawn_cli(config, data, ["failed.zip"], fail_processing=True)
        self.expect_started(failed, config, {"seed": 2}, ["failed.zip"])
        saved = {"seed": 2, "failed.zip": 1}
        self.expect(failed, ("upload", config.name, saved))
        submitter = self.spawn_cli(config, data, ["pending.zip"])
        self.finish(submitter)
        failed[1].send("finish")
        self.expect(failed, ("reported_error", "OSError", "fixture extraction failure"))
        self.finish(failed, result=1)
        successor = self.spawn_cli(config, data, ["resume.zip"])
        self.expect_started(successor, config, saved, ["resume.zip"])
        self.expect(successor, ("processed", ["pending.zip"]))
        expected = {**saved, "resume.zip": 1, "pending.zip": 1}
        self.expect(successor, ("upload", config.name, expected))
        successor[1].send("finish")
        self.finish(successor)
        self.assertEqual(json.loads((data / "dict.json").read_text(encoding="utf-8")), expected)

    def test_different_configs_sharing_data_wait_before_loading_and_keep_own_queues(self):
        first_config, data = self.fixture("first-config")
        second_config, _ = self.fixture("second-config")
        first = self.spawn_cli(first_config, data, ["first.zip"])
        self.expect_started(first, first_config, {"seed": 2}, ["first.zip"])
        first_book = {"seed": 2, "first.zip": 1}
        self.expect(first, ("upload", first_config.name, first_book))
        second = self.spawn_cli(second_config, data, ["second.zip"])
        self.expect(second, ("waiting_for_book",))
        # It already owns its config queue while awaiting the shared book lock.
        submitter = self.spawn_cli(second_config, data, ["second-queued.zip"])
        self.finish(submitter)
        first[1].send("finish")
        self.finish(first)
        self.expect_started(second, second_config, first_book, ["second.zip"])
        self.expect(second, ("processed", ["second-queued.zip"]))
        final_book = {**first_book, "second.zip": 1, "second-queued.zip": 1}
        self.expect(second, ("upload", second_config.name, final_book))
        second[1].send("finish")
        self.finish(second)
        self.assertEqual(json.loads((data / "dict.json").read_text(encoding="utf-8")), final_book)

    def test_different_data_directories_do_not_wait_for_each_others_upload(self):
        first_config, first_data = self.fixture("first", "first-data")
        second_config, second_data = self.fixture("second", "second-data")
        first = self.spawn_cli(first_config, first_data, ["first.zip"])
        self.expect_started(first, first_config, {"seed": 2}, ["first.zip"])
        self.expect(first, ("upload", first_config.name, {"seed": 2, "first.zip": 1}))
        second = self.spawn_cli(second_config, second_data, ["second.zip"])
        self.expect_started(second, second_config, {"seed": 2}, ["second.zip"])
        self.expect(second, ("upload", second_config.name, {"seed": 2, "second.zip": 1}))
        # The second finishes while the first still awaits an explicit release.
        second[1].send("finish")
        self.finish(second)
        first[1].send("finish")
        self.finish(first)


class ImportAndHelpTests(unittest.TestCase):
    def test_import_does_not_start_workers_or_register_sync(self):
        command = (
            "import atexit, multiprocessing, requests\n"
            "from unittest.mock import patch\n"
            "with patch.object(atexit, 'register', wraps=atexit.register) as hook, "
            "patch.object(requests, 'get', side_effect=AssertionError('network')), "
            "patch.object(requests, 'patch', side_effect=AssertionError('network')):\n"
            " import auto_decompression\n"
            " assert not multiprocessing.active_children()\n"
            " assert not any(getattr(c.args[0], '__module__', '') in "
            "('auto_decompression', 'passwords') for c in hook.call_args_list)\n"
        )
        # A fresh interpreter exercises Windows spawn-style import semantics.
        result = subprocess.run([sys.executable, "-B", "-c", command], cwd=Path(cli.__file__).parent, capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_script_help_retains_options(self):
        result = subprocess.run([sys.executable, "-B", str(Path(cli.__file__)), "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ("--embedded-scan-depth", "--flatten-single-file", "--trash-on-success", "--config-dir", "--show-paths", "--update-dict", "--check-dict-conflict-on-startup", "--use-binwalk", "--password-workers"):
            self.assertIn(option, result.stdout)


class DictionaryUpdateTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))
        self.temp = tempfile.TemporaryDirectory(prefix="autodec-update-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = root / "config"
        self.data = root / "data"
        self.config.mkdir()
        self.data.mkdir()
        self.local = self.data / "dict.json"
        self.original_bytes = b'{ "valuable-local-password": 9 }\n'
        self.local.write_bytes(self.original_bytes)
        (self.config / "gist_config.json").write_text(json.dumps({
            "token": "fixture-token", "gist_id": "fixture-id", "file": "dict.json"
        }), encoding="utf-8")
        self.enterContext(patch.object(cli, "PlatformDirs", return_value=SimpleNamespace(
            user_config_dir=str(self.config), user_data_dir=str(self.data)
        )))
        self.enterContext(patch.object(cli.time, "sleep"))
        self.enterContext(patch("requests.sessions.Session.request", side_effect=AssertionError("unexpected network request")))
        self.report = self.enterContext(patch.object(cli, "error_end"))
        self.fetch = self.enterContext(patch.object(cli.PasswordBook, "_fetch_from_gist"))
        self.upload = self.enterContext(patch.object(cli.PasswordBook, "_update_gist", return_value=True))

    def test_failed_pull_keeps_local_bytes_and_never_uploads(self):
        self.fetch.return_value = (None, None)
        self.assertEqual(cli.main(["--update-dict"]), 1)
        self.assertEqual(self.local.read_bytes(), self.original_bytes)
        self.upload.assert_not_called()
        self.fetch.assert_called_once()
        self.report.assert_not_called()

    def test_pull_parse_exception_also_never_uploads(self):
        self.fetch.return_value = ({"remote": 4}, "invalid-timestamp")
        self.assertEqual(cli.main(["--update-dict"]), 1)
        self.assertEqual(self.local.read_bytes(), self.original_bytes)
        self.upload.assert_not_called()
        self.fetch.assert_called_once()
        self.report.assert_called_once()

    def test_successful_pull_replaces_local_dictionary_without_uploading(self):
        self.fetch.return_value = ({"remote": 4}, "2026-01-01T00:00:00Z")
        self.assertEqual(cli.main(["--update-dict"]), 0)
        self.assertEqual(json.loads(self.local.read_text(encoding="utf-8")), {"remote": 4})
        self.upload.assert_not_called()
        self.fetch.assert_called_once()
        self.report.assert_not_called()

    def test_invalid_local_dictionary_cannot_be_uploaded_by_normal_exit(self):
        original = b'{ "valuable-local-password": '
        self.local.write_bytes(original)
        with patch.object(cli, "ExtractionWorkflow") as workflow:
            self.assertEqual(cli.main(["input.zip"]), 1)
        self.assertEqual(self.local.read_bytes(), original)
        self.upload.assert_not_called()
        workflow.assert_not_called()

    def test_startup_skip_sync_preserves_local_dictionary_and_still_processes(self):
        self.fetch.return_value = ({"remote": 4}, "2026-01-01T00:00:00Z")
        workflow = Mock(embedded_scan_depth=2)
        with patch("builtins.input", return_value="3"), patch.object(cli.InstanceQueue, "start"), patch.object(cli, "ExtractionWorkflow", return_value=workflow):
            self.assertEqual(cli.main(["--check-dict-conflict-on-startup", "input.zip"]), 0)
        workflow.process_files.assert_called_once_with(["input.zip"])
        self.assertEqual(self.local.read_bytes(), self.original_bytes)
        self.upload.assert_not_called()
        self.fetch.assert_called_once()



if __name__ == "__main__":
    unittest.main()
