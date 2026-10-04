"""CLI orchestration with isolated paths and explicit service substitutes."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import auto_decompression as cli


class CliTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))
        self.temp = tempfile.TemporaryDirectory(prefix="autodec-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directories = SimpleNamespace(user_config_dir=str(self.root / "config"), user_data_dir=str(self.root / "data"))
        self.book = Mock()
        self.book.skip_gist_sync = False
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

    def test_primary_processes_batches_before_single_exit_sync(self):
        self.queue.next_batch.side_effect = [["later.rar"], []]
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
        self.book.sync_to_gist_before_exit.assert_called_once()

    def test_failed_update_preserves_failure_exit(self):
        self.book.pull_from_gist_if_possible.return_value = False
        self.assertEqual(cli.main(["--update-dict"]), 1)
        self.queue.start.assert_not_called()

    def test_startup_conflict_check_remains_opt_in(self):
        self.assertEqual(cli.main(["--check-dict-conflict-on-startup", "file.zip"]), 0)
        self.book.check_dict_conflict_on_startup.assert_called_once()

    def test_password_entry_keeps_zero_initial_count_and_accepts_queued_files(self):
        self.queue.next_batch.side_effect = [["arrived.zip"], []]
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
        for option in ("--embedded-scan-depth", "--flatten-single-file", "--trash-on-success", "--config-dir", "--show-paths", "--update-dict", "--check-dict-conflict-on-startup", "--use-binwalk"):
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


if __name__ == "__main__":
    unittest.main()
