"""Extractor result handling and isolated real-tool round trips."""

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import extraction
import extract_hidden_zip
from passwords import PasswordBook
from workflow import ExtractionWorkflow


class ExtractorResults(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))
        self.temp = tempfile.TemporaryDirectory(prefix="autodec-backend-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "sample.zip"
        self.source.write_bytes(b"fixture")
        self.target = self.root / "output"
        self.target.mkdir()

    def run_7zip(self, code, stdout="", stderr=""):
        process = Mock()
        process.stdout = io.StringIO(stdout)
        process.stderr = io.StringIO(stderr)
        process.poll.return_value = code
        process.wait.return_value = code
        with patch.object(extraction.subprocess, "Popen", return_value=process), patch.object(extraction, "Progress"):
            result = extraction.extract_with_7zip(str(self.source), str(self.target), "fixture")
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertTrue(process.wait.called)
        process.terminate.assert_not_called()
        return result

    def test_nonzero_without_stderr_is_failure(self):
        self.assertEqual(self.run_7zip(7), -3)

    def test_zero_exit_is_success(self):
        self.assertEqual(self.run_7zip(0, "Everything is Ok\n"), 1)

    def test_error_categories(self):
        for message, expected in [("ERROR: Wrong password\n", -1), ("Cannot open the file as archive\n", -2), ("Can not open encrypted archive\n", -2), ("Disk write error\n", -3)]:
            with self.subTest(message=message):
                self.assertEqual(self.run_7zip(2, stderr=message), expected)

    def test_password_prompt_buffered_after_exit_is_not_dropped(self):
        self.assertEqual(self.run_7zip(255, "Enter password (will not be echoed):\n", "Break signaled\n"), -1)

    def test_stderr_is_not_silent_success(self):
        self.assertEqual(self.run_7zip(0, stderr="unexpected diagnostic\n"), -3)

    def test_bandizip_nonzero_with_partial_output_is_failure(self):
        def partial(*args, **kwargs):
            (self.target / "partial.txt").write_bytes(b"incomplete")
            return subprocess.CompletedProcess(args[0], 2, "extraction failed", "")
        with patch.object(extraction.subprocess, "run", side_effect=partial):
            self.assertEqual(extraction.extract_with_bandizip(str(self.source), str(self.target)), -3)

    def test_bandizip_classifies_lowercased_unknown_archive(self):
        result = subprocess.CompletedProcess([], 2, "Unknown archive", "")
        with patch.object(extraction.subprocess, "run", return_value=result):
            self.assertEqual(extraction.extract_with_bandizip(str(self.source), str(self.target)), -2)

    def test_dictionary_attempt_order_and_skip(self):
        with patch.object(extraction, "extract_with_7zip", side_effect=[-1, 1]) as backend:
            result = extraction.try_passwords("input", "output", [("most", 9), ("middle", 5), ("last", 1)], "middle")
        self.assertEqual(result, "last")
        self.assertEqual([call.args[2] for call in backend.call_args_list], ["most", "last"])

    def test_manual_entry_retries_and_can_skip(self):
        with patch("builtins.input", side_effect=["bad", "good"]), patch.object(extraction, "extract_with_7zip", side_effect=[-1, 1]) as backend:
            self.assertEqual(extraction.manual_password_entry("input", "output", 2), "good")
        self.assertEqual([call.args[2] for call in backend.call_args_list], ["bad", "good"])
        with patch("builtins.input", return_value=""), patch.object(extraction, "extract_with_7zip") as backend:
            self.assertIsNone(extraction.manual_password_entry("input", "output", 2))
        backend.assert_not_called()


@unittest.skipUnless(shutil.which("7z"), "7z is required for isolated integration tests")
class RealExtractorRoundTrips(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))
        self.temp = tempfile.TemporaryDirectory(prefix="autodec-roundtrip-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build = self.root / "build"
        self.build.mkdir()
        self.payload = self.build / "内容.txt"
        self.data = b"payload bytes\n" * 3000
        self.payload.write_bytes(self.data)
        self.counter = 0
        for directory in ("config", "data", "legacy"):
            (self.root / directory).mkdir()
        self.book = PasswordBook(str(self.root / "config"), str(self.root / "data"), str(self.root / "legacy"))

    def target(self):
        self.counter += 1
        target = self.root / f"output-{self.counter}"
        target.mkdir()
        return target

    def create_archive(self, name, *options):
        archive = self.root / name
        result = subprocess.run(
            ["7z", "a", str(archive), str(self.payload), *options],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        return archive

    def test_plain_unicode_round_trip(self):
        archive = self.create_archive("sample.zip")
        target = self.target()
        self.assertEqual(extraction.extract_with_7zip(str(archive), str(target)), 1)
        self.assertEqual((target / self.payload.name).read_bytes(), self.data)

    def test_encrypted_archive_missing_wrong_and_correct_password(self):
        archive = self.create_archive("encrypted.zip", "-pfixture-key")
        for password in (None, "wrong"):
            with self.subTest(password=password):
                self.assertEqual(extraction.extract_with_7zip(str(archive), str(self.target()), password), -1)
        target = self.target()
        self.assertEqual(extraction.extract_with_7zip(str(archive), str(target), "fixture-key"), 1)
        self.assertEqual((target / self.payload.name).read_bytes(), self.data)

    def test_empty_book_can_use_archive_name_password(self):
        archive = self.create_archive("fixture-key.zip", "-pfixture-key")
        runner = ExtractionWorkflow(self.book, trash_on_success=False, embedded_scan_depth=0)
        with patch("builtins.input", side_effect=AssertionError("automatic name fallback must succeed")):
            runner.process_files([str(archive)])
        self.assertEqual((self.root / "fixture-key" / self.payload.name).read_bytes(), self.data)
        self.assertTrue(archive.exists())
        self.assertIn("fixture-key", self.book.passwords)

    def test_nonarchive_is_not_openable(self):
        self.assertEqual(extraction.extract_with_7zip(str(self.payload), str(self.target())), -2)

    def test_split_input_redirects_and_preserves_payload(self):
        self.data = random.Random(31).randbytes(8000)
        self.payload.write_bytes(self.data)
        self.create_archive("split.7z", "-v2k")
        parts = sorted(self.root.glob("split.7z.*"))
        self.assertGreater(len(parts), 1)
        runner = ExtractionWorkflow(self.book, trash_on_success=False, embedded_scan_depth=0)
        runner.process_files([str(parts[1]), str(parts[0])])
        self.assertEqual((self.root / "split" / self.payload.name).read_bytes(), self.data)
        self.assertTrue(all(part.exists() for part in parts))
        self.assertFalse((self.root / "split~1").exists())

    def test_manual_signature_carving_preserves_archive_bytes(self):
        archive = self.create_archive("embedded.zip")
        carrier = self.root / "carrier.bin"
        prefix = b"not an archive header\n" * 20
        carrier.write_bytes(prefix + archive.read_bytes())
        recovered = self.root / "recovered.zip"
        with patch.object(extract_hidden_zip, "USE_BINWALK", False):
            self.assertTrue(extract_hidden_zip.has_embedded_signature(str(carrier), "zip"))
            extract_hidden_zip.extract_embedded_file(str(carrier), str(recovered), "zip")
        self.assertEqual(recovered.read_bytes(), archive.read_bytes())
        target = self.target()
        self.assertEqual(extraction.extract_with_7zip(str(recovered), str(target)), 1)
        self.assertEqual((target / self.payload.name).read_bytes(), self.data)

    @unittest.skipUnless(shutil.which("bz"), "Bandizip console tool is required")
    def test_bandizip_plain_and_encrypted_round_trip(self):
        archive = self.create_archive("bandizip.zip", "-pfixture-key")
        self.assertEqual(extraction.extract_with_bandizip(str(archive), str(self.target()), "wrong"), -1)
        target = self.target()
        self.assertEqual(extraction.extract_with_bandizip(str(archive), str(target), "fixture-key"), 1)
        self.assertEqual((target / self.payload.name).read_bytes(), self.data)


if __name__ == "__main__":
    unittest.main()
