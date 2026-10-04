"""Local dictionary and Gist policy tests; all network calls are replaced."""

from contextlib import redirect_stdout
import io
import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import passwords
from passwords import PasswordBook


class PasswordBookTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))
        self.temp = tempfile.TemporaryDirectory(prefix="autodec-passwords-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config, self.data, self.legacy = [self.root / name for name in ("config", "data", "legacy")]
        for directory in (self.config, self.data, self.legacy):
            directory.mkdir()
        self.book = PasswordBook(str(self.config), str(self.data), str(self.legacy))
        self.book.gist_config = {"token": "fixture-token", "gist_id": "fixture-id", "file": "dict.json"}
        # Any unplanned network operation fails the test rather than touching a service.
        for method in ("get", "post", "patch"):
            blocker = patch.object(passwords.requests, method, side_effect=AssertionError("unexpected network request"))
            blocker.start()
            self.addCleanup(blocker.stop)
        delay = patch.object(passwords.time, "sleep")
        delay.start()
        self.addCleanup(delay.stop)

    def save(self, dictionary):
        self.book.passwords = dictionary
        self.book.save_passwords()

    def test_count_updates_and_unicode_round_trip(self):
        self.book.add_password("密码", 0)
        self.book.add_password("密码")
        self.book.add_password(None)
        self.book.save_passwords()
        other = PasswordBook(str(self.config), str(self.data), str(self.legacy))
        other.read_passwords()
        self.assertEqual(other.passwords, {"密码": 1})

    def test_config_override_does_not_move_dictionary(self):
        self.save({"local": 4})
        self.assertTrue((self.data / "dict.json").is_file())
        self.assertFalse((self.config / "dict.json").exists())

    def test_migration_prefers_valid_config_book_and_keeps_legacy_files(self):
        (self.config / "dict.json").write_text(json.dumps({"config": 3}), encoding="utf-8")
        (self.legacy / "dict.json").write_text(json.dumps({"legacy": 9}), encoding="utf-8")
        self.book.check_passwords()
        self.assertEqual(self.book.passwords, {"config": 3})
        self.assertTrue((self.config / "dict.json").exists())
        self.assertTrue((self.legacy / "dict.json").exists())

    def test_invalid_legacy_book_is_skipped(self):
        (self.config / "dict.json").write_text('["not a dictionary"]', encoding="utf-8")
        (self.legacy / "dict.json").write_text('{"valid": 2}', encoding="utf-8")
        self.book.check_passwords()
        self.assertEqual(self.book.passwords, {"valid": 2})

    def test_missing_local_book_pulls_remote_and_persists(self):
        with patch.object(self.book, "_fetch_from_gist", return_value=({"remote": 5}, "2026-01-01T00:00:00Z")):
            self.book.check_passwords()
        self.assertEqual(json.loads(Path(self.book.path).read_text(encoding="utf-8")), {"remote": 5})
        self.assertIsNotNone(self.book.gist_remote_ts)

    def test_missing_local_and_unavailable_remote_yields_empty_book(self):
        with patch.object(self.book, "_fetch_from_gist", return_value=(None, None)):
            self.book.check_passwords()
        self.assertEqual(self.book.passwords, {})

    def test_startup_conflict_default_pulls_remote(self):
        self.save({"local": 2})
        with patch.object(self.book, "_fetch_from_gist", return_value=({"remote": 3}, "2026-01-01T00:00:00Z")), patch("builtins.input", return_value=""):
            self.book.check_dict_conflict_on_startup()
        self.assertEqual(self.book.passwords, {"remote": 3})
        self.assertEqual(json.loads(Path(self.book.path).read_text(encoding="utf-8")), {"remote": 3})

    def test_startup_conflict_upload_retains_local(self):
        self.save({"local": 2})
        with patch.object(self.book, "_fetch_from_gist", side_effect=[({"remote": 3}, "2026-01-01T00:00:00Z"), ({"local": 2}, "2026-01-02T00:00:00Z")]), patch.object(self.book, "_update_gist", return_value=True) as upload, patch("builtins.input", return_value="2"):
            self.book.check_dict_conflict_on_startup()
        self.assertEqual(json.loads(upload.call_args.args[1]), {"local": 2})
        self.assertEqual(self.book.passwords, {"local": 2})
        self.assertEqual(self.book.gist_remote_ts.day, 2)

    def test_startup_skip_prevents_exit_sync(self):
        self.save({"local": 2})
        with patch.object(self.book, "_fetch_from_gist", return_value=({"remote": 3}, "2026-01-01T00:00:00Z")), patch("builtins.input", return_value="3"):
            self.book.check_dict_conflict_on_startup()
        with patch.object(self.book, "_fetch_from_gist") as fetch, patch.object(self.book, "_update_gist") as upload:
            self.book.sync_to_gist_before_exit()
        fetch.assert_not_called()
        upload.assert_not_called()
        self.assertEqual(self.book.passwords, {"local": 2})

    def test_exit_conflict_warning_preserves_existing_upload_policy(self):
        self.save({"local": 2})
        self.book.gist_remote_ts = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        with patch.object(self.book, "_fetch_from_gist", return_value=({"remote": 9}, "2099-01-01T00:00:00Z")), patch.object(self.book, "_update_gist", return_value=True) as upload:
            self.book.sync_to_gist_before_exit()
        self.assertEqual(json.loads(upload.call_args.args[1]), {"local": 2})

    def test_gist_transport_decodes_dictionary_and_updates_named_file(self):
        cfg = self.book.gist_config
        response = Mock(status_code=200)
        response.json.return_value = {"files": {"dict.json": {"content": '{"remote": 4}'}}, "updated_at": "2026-01-01T00:00:00Z"}
        with patch.object(passwords.requests, "get", return_value=response) as get:
            self.assertEqual(self.book._fetch_from_gist(cfg), ({"remote": 4}, "2026-01-01T00:00:00Z"))
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer fixture-token")
        with patch.object(passwords.requests, "patch", return_value=Mock(status_code=200)) as upload:
            self.assertTrue(self.book._update_gist(cfg, '{"local": 3}'))
        self.assertEqual(upload.call_args.kwargs["json"]["files"]["dict.json"]["content"], '{"local": 3}')

    def test_existing_gist_config_needs_no_interactive_setup(self):
        cfg = dict(self.book.gist_config)
        (self.config / "gist_config.json").write_text(json.dumps(cfg), encoding="utf-8")
        with patch("builtins.input", side_effect=AssertionError("unexpected prompt")):
            self.assertEqual(self.book.ensure_gist_config(), cfg)

    def test_setup_upload_choice_loads_local_book_before_exit(self):
        self.save({"local": 8})
        self.book.passwords = {}
        with patch.object(self.book, "_fetch_from_gist", return_value=({"remote": 2}, "2026-01-01T00:00:00Z")), patch("builtins.input", side_effect=["fixture-token", "fixture-id", "2"]):
            self.book.ensure_gist_config()
            self.book.check_passwords()
        self.assertEqual(self.book.passwords, {"local": 8})
        with patch.object(self.book, "_fetch_from_gist", return_value=({"remote": 2}, "2026-01-01T00:00:00Z")), patch.object(self.book, "_update_gist", return_value=True) as upload:
            self.book.sync_to_gist_before_exit()
        self.assertEqual(json.loads(upload.call_args.args[1]), {"local": 8})


if __name__ == "__main__":
    unittest.main()
