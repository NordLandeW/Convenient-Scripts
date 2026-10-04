"""Single-chain extraction and publication behavior with safe filesystem fixtures.

The backend fixture only materializes an archive's listed members. Selection,
recursion, naming, publication and cleanup all run through the real workflow.
No test invokes an external extractor, a network request or the system trash.
"""

from contextlib import redirect_stdout
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

import workflow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "input"
        self.base.mkdir()
        self.trash = self.root / "trash"
        self.trash.mkdir()
        self.archives = {}
        self.attempts = []
        self.recycled = []
        self.book = Mock()
        self.book.passwords = {"known": 3}
        self.enterContext(redirect_stdout(io.StringIO()))
        self.enterContext(patch("send2trash.send2trash", side_effect=self.recycle))
        self.backend = self.enterContext(patch.object(
            workflow, "extract_with_7zip", side_effect=self.extract
        ))
        self.dictionary = self.enterContext(patch.object(
            workflow, "try_passwords", return_value=None
        ))
        self.manual = self.enterContext(patch.object(
            workflow, "manual_password_entry", return_value=None
        ))
        self.bandizip = self.enterContext(patch.object(
            workflow, "handle_bandizip_extraction", return_value=None
        ))
        self.signature = self.enterContext(patch.object(
            workflow.hiddenZip, "has_embedded_signature", return_value=False
        ))
        self.hidden_extract = self.enterContext(patch.object(
            workflow.hiddenZip, "extract_embedded_file",
            side_effect=AssertionError("Unexpected hidden extraction")
        ))
        self.enterContext(patch.object(workflow.hiddenZip, "USE_BINWALK", False))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError(
            "Unit fixtures must not launch external processes"
        )))
        self.enterContext(patch("builtins.input", side_effect=AssertionError(
            "Unit fixtures must not prompt"
        )))
        self.enterContext(patch("requests.sessions.Session.request", side_effect=AssertionError(
            "Unit fixtures must not access the network"
        )))

    @staticmethod
    def tree(root):
        return {
            path.relative_to(root).as_posix(): None if path.is_dir() else path.read_bytes()
            for path in root.rglob("*")
        }

    @staticmethod
    def materialize(destination, members):
        for name, data in members.items():
            path = Path(destination) / name
            if data is None:
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

    def source(self, name="outer.zip", data=b"original archive"):
        path = self.base / name
        path.write_bytes(data)
        return str(path)

    def extract(self, filename, destination, password=None):
        self.assertTrue(Path(filename).resolve().is_relative_to(self.root.resolve()))
        self.assertTrue(Path(destination).resolve().is_relative_to(self.root.resolve()))
        self.attempts.append((Path(filename).name, password))
        result = self.archives.get(Path(filename).name, -2)
        if callable(result):
            return result(filename, destination, password)
        if isinstance(result, int):
            return result
        self.materialize(destination, result)
        return 1

    def recycle(self, filename):
        path = Path(filename)
        self.assertTrue(path.resolve().is_relative_to(self.base.resolve()))
        self.assertTrue(path.exists(), "Recycled paths must still exist")
        contents = self.tree(path) if path.is_dir() else path.read_bytes()
        self.recycled.append((path.name, contents))
        shutil.move(str(path), str(self.trash / str(len(self.recycled))))

    def run_files(self, *paths, **options):
        extractor = workflow.ExtractionWorkflow(self.book, **options)
        extractor.process_files(list(paths))
        return extractor

    def names_attempted(self):
        return [name for name, _ in self.attempts]

    def test_two_archive_candidates_stop_and_preserve_both(self):
        source = self.source()
        self.archives["outer.zip"] = {"one.zip": b"first", "two.zip": b"second"}
        self.run_files(source)
        self.assertEqual(self.names_attempted(), ["outer.zip"])
        self.assertEqual(self.tree(self.base), {
            "outer": None, "outer/one.zip": b"first", "outer/two.zip": b"second"
        })
        self.assertEqual(self.recycled, [("outer.zip", b"original archive")])
        self.book.save_passwords.assert_called_once()

    def test_single_chain_discards_small_noise_and_parallel_directories(self):
        source = self.source()
        self.archives.update({
            "outer.zip": {"inner.zip": b"inner", "readme.txt": b"r" * (20 * 1024),
                          "extras/keep.txt": b"discarded", "empty": None},
            "inner.zip": {"a.txt": b"a", "b.txt": b"b"},
        })
        self.run_files(source)
        self.assertEqual(self.names_attempted(), ["outer.zip", "inner.zip"])
        self.assertEqual(self.tree(self.base), {
            "inner": None, "inner/a.txt": b"a", "inner/b.txt": b"b"
        })
        self.assertEqual(self.recycled, [("outer.zip", b"original archive")])

    def test_noise_above_threshold_becomes_second_candidate(self):
        source = self.source()
        noise = b"r" * (20 * 1024 + 1)
        self.archives["outer.zip"] = {"inner.zip": b"inner", "readme.txt": noise}
        self.run_files(source)
        self.assertEqual(self.names_attempted(), ["outer.zip"])
        self.assertEqual(self.tree(self.base), {
            "outer": None, "outer/inner.zip": b"inner", "outer/readme.txt": noise
        })

    def test_unrecognized_large_file_is_attempted_then_preserved(self):
        source = self.source()
        payload = b"payload" * 4000
        self.archives["outer.zip"] = {"payload.bin": payload, "readme.txt": b"note"}
        self.run_files(source, embedded_scan_depth=0)
        self.assertEqual(self.names_attempted(), ["outer.zip", "payload.bin"])
        self.assertEqual(self.tree(self.base), {
            "outer": None, "outer/payload.bin": payload, "outer/readme.txt": b"note"
        })
        self.signature.assert_not_called()

    def test_only_directory_chain_updates_effective_output_name(self):
        source = self.source()
        self.archives["outer.zip"] = {"wrapper/Actual/a.txt": b"a", "wrapper/Actual/b.txt": b"b"}
        self.run_files(source)
        self.assertEqual(self.tree(self.base), {
            "Actual": None, "Actual/a.txt": b"a", "Actual/b.txt": b"b"
        })

    def test_direct_small_file_prevents_directory_descent(self):
        source = self.source()
        self.archives["outer.zip"] = {"Actual/a.txt": b"a", "readme.txt": b"note"}
        self.run_files(source)
        self.assertEqual(self.tree(self.base), {
            "outer": None, "outer/Actual": None,
            "outer/Actual/a.txt": b"a", "outer/readme.txt": b"note"
        })
        self.assertEqual(self.names_attempted(), ["outer.zip"])

    def test_multiple_directories_stop_descent(self):
        source = self.source()
        self.archives["outer.zip"] = {"left/a.txt": b"a", "right/b.txt": b"b"}
        self.run_files(source)
        self.assertEqual(self.tree(self.base), {
            "outer": None, "outer/left": None, "outer/left/a.txt": b"a",
            "outer/right": None, "outer/right/b.txt": b"b"
        })

    def test_inner_password_skip_preserves_whole_parent_layer(self):
        source = self.source()
        self.archives.update({
            "outer.zip": {"locked.zip": b"locked", "note.txt": b"note", "extras/a": b"a"},
            "locked.zip": -1,
        })
        self.run_files(source)
        self.assertEqual(self.tree(self.base), {
            "outer": None, "outer/locked.zip": b"locked", "outer/note.txt": b"note",
            "outer/extras": None, "outer/extras/a": b"a"
        })
        self.assertEqual(self.recycled, [("outer.zip", b"original archive")])
        self.manual.assert_called_once()
        self.assertEqual(self.manual.call_args.args[2], 2)

    def test_top_level_password_skip_keeps_source(self):
        source = self.source()
        self.archives["outer.zip"] = -1
        self.run_files(source)
        self.assertEqual(self.tree(self.base), {"outer.zip": b"original archive"})
        self.assertEqual(self.recycled, [])
        self.manual.assert_called_once()

    def test_backend_failure_does_not_publish_partial_files_or_recycle_source(self):
        source = self.source()

        def fail_after_partial_output(filename, destination, password):
            self.materialize(destination, {"partial.txt": b"incomplete"})
            return -3

        self.archives["outer.zip"] = fail_after_partial_output
        self.run_files(source)
        self.assertEqual(self.tree(self.base), {"outer.zip": b"original archive"})
        self.assertEqual(self.recycled, [])
        self.book.add_password.assert_not_called()

    def test_inner_backend_failure_preserves_parent_contents(self):
        source = self.source()
        self.archives.update({
            "outer.zip": {"broken.zip": b"broken", "readme.txt": b"note"},
            "broken.zip": -3,
        })
        self.run_files(source)
        self.assertEqual(self.tree(self.base), {
            "outer": None, "outer/broken.zip": b"broken", "outer/readme.txt": b"note"
        })
        self.assertEqual(self.recycled, [("outer.zip", b"original archive")])

    def test_empty_password_book_can_extract_without_an_initial_password(self):
        source = self.source()
        self.book.passwords = {}
        self.archives["outer.zip"] = {"a.txt": b"a", "b.txt": b"b"}
        self.run_files(source)
        self.assertEqual(self.attempts, [("outer.zip", None)])
        self.assertEqual(self.tree(self.base), {
            "outer": None, "outer/a.txt": b"a", "outer/b.txt": b"b"
        })
        self.assertEqual(self.recycled, [("outer.zip", b"original archive")])

    def test_secondary_split_inputs_extract_once_and_recycle_all_parts(self):
        later = self.source("pack.7z.010", b"second")
        primary = self.source("pack.7z.002", b"first")
        unrelated = self.source("pack.notes", b"keep")
        self.archives["pack.7z.002"] = {"a.txt": b"a", "b.txt": b"b"}
        self.run_files(later, primary, later)
        self.assertEqual(self.names_attempted(), ["pack.7z.002"])
        self.assertEqual(self.tree(self.base), {
            "pack": None, "pack/a.txt": b"a", "pack/b.txt": b"b", "pack.notes": b"keep"
        })
        self.assertCountEqual(self.recycled, [("pack.7z.002", b"first"), ("pack.7z.010", b"second")])
        self.assertEqual(Path(unrelated).read_bytes(), b"keep")

    def test_nested_split_set_counts_as_one_candidate(self):
        source = self.source()
        self.archives.update({
            "outer.zip": {"pack.part02.rar": b"first", "pack.part10.rar": b"second", "note.txt": b"noise"},
            "pack.part02.rar": {"a.txt": b"a", "b.txt": b"b"},
        })
        self.run_files(source)
        self.assertEqual(self.names_attempted(), ["outer.zip", "pack.part02.rar"])
        self.assertEqual(self.tree(self.base), {
            "pack": None, "pack/a.txt": b"a", "pack/b.txt": b"b"
        })

    def test_extensionless_source_replaced_by_directory_is_not_recycled_again(self):
        source = self.source("bundle")
        self.archives["bundle"] = {"a.txt": b"a", "b.txt": b"b"}
        self.run_files(source)
        self.assertEqual(self.tree(self.base), {
            "bundle": None, "bundle/a.txt": b"a", "bundle/b.txt": b"b"
        })
        self.assertEqual(self.recycled, [("bundle", b"original archive")])

    def test_source_replaced_by_same_named_file_is_not_recycled_again(self):
        source = self.source()
        # The second call sees the extracted terminal file, not the source archive.
        def outer_or_payload(filename, destination, password):
            if Path(filename).parent == self.base:
                self.materialize(destination, {"outer.zip": b"terminal data"})
                return 1
            return -2

        self.archives["outer.zip"] = outer_or_payload
        self.run_files(source, embedded_scan_depth=0)
        self.assertEqual(self.tree(self.base), {"outer.zip": b"terminal data"})
        self.assertEqual(self.recycled, [("outer.zip", b"original archive")])

    def test_disabled_recycling_retains_source_and_uses_unique_output_directory(self):
        source = self.source("bundle")
        self.archives["bundle"] = {"a.txt": b"a", "b.txt": b"b"}
        self.run_files(source, trash_on_success=False)
        self.assertEqual(self.tree(self.base), {
            "bundle": b"original archive", "bundle~1": None,
            "bundle~1/a.txt": b"a", "bundle~1/b.txt": b"b"
        })
        self.assertEqual(self.recycled, [])

    def test_apk_is_skipped_only_as_top_level_input(self):
        apk = self.source("external.apk", b"untouched")
        source = self.source()
        self.archives.update({
            "outer.zip": {"nested.apk": b"apk" * 8000},
            "nested.apk": {"a.txt": b"a", "b.txt": b"b"},
        })
        self.run_files(apk, source)
        self.assertEqual(self.names_attempted(), ["outer.zip", "nested.apk"])
        self.assertEqual(self.tree(self.base), {
            "external.apk": b"untouched", "nested": None,
            "nested/a.txt": b"a", "nested/b.txt": b"b"
        })
        self.assertEqual(self.recycled, [("outer.zip", b"original archive")])

    def test_normal_recursion_is_not_limited_by_hidden_scan_depth(self):
        source = self.source()
        self.archives.update({
            "outer.zip": {"middle.zip": b"middle"},
            "middle.zip": {"inner.zip": b"inner"},
            "inner.zip": {"deep.zip": b"deep"},
            "deep.zip": {"a.txt": b"a", "b.txt": b"b"},
        })
        self.run_files(source, embedded_scan_depth=0)
        self.assertEqual(self.names_attempted(), ["outer.zip", "middle.zip", "inner.zip", "deep.zip"])
        self.signature.assert_not_called()
        self.assertEqual(self.tree(self.base), {
            "deep": None, "deep/a.txt": b"a", "deep/b.txt": b"b"
        })

    def test_hidden_scan_at_depth_limit_uses_ordered_formats(self):
        source = self.source()
        payload = b"disguised" * 4000
        self.archives["outer.zip"] = {"payload.bin": payload}
        self.run_files(source, embedded_scan_depth=2)
        self.assertEqual([call.args[1] for call in self.signature.call_args_list],
                         ["zip", "rar", "7z", "*"])
        self.assertTrue(all(Path(call.args[0]).name == "payload.bin"
                            for call in self.signature.call_args_list))
        self.assertEqual(self.tree(self.base), {"outer": None, "outer/payload.bin": payload})

    def test_hidden_scan_beyond_depth_limit_is_not_attempted(self):
        source = self.source()
        payload = b"disguised" * 4000
        self.archives["outer.zip"] = {"payload.bin": payload}
        self.run_files(source, embedded_scan_depth=1)
        self.signature.assert_not_called()
        self.assertEqual(self.tree(self.base), {"outer": None, "outer/payload.bin": payload})

    def test_hidden_recovery_retries_7zip_and_removes_recovered_file(self):
        source = self.source("cover.bin")
        self.signature.side_effect = lambda filename, fmt: fmt == "rar"
        self.hidden_extract.side_effect = lambda source, target, fmt: Path(target).write_bytes(b"recovered")
        self.archives["cover.bin.AutoDecRecovered"] = {"a.txt": b"a", "b.txt": b"b"}
        self.run_files(source, embedded_scan_depth=1)
        self.assertEqual(self.names_attempted(), ["cover.bin", "cover.bin.AutoDecRecovered"])
        self.assertEqual([call.args[1] for call in self.signature.call_args_list], ["zip", "rar"])
        self.bandizip.assert_not_called()
        self.assertEqual(self.tree(self.base), {
            "cover": None, "cover/a.txt": b"a", "cover/b.txt": b"b"
        })
        self.assertEqual(self.recycled, [("cover.bin", b"original archive")])

    def test_bandizip_fallback_is_used_for_unopenable_recovered_file(self):
        source = self.source("cover.bin")
        self.signature.side_effect = lambda filename, fmt: fmt == "zip"
        self.hidden_extract.side_effect = lambda source, target, fmt: Path(target).write_bytes(b"recovered")

        def bandizip(filename, destination, passwords, level):
            self.materialize(destination, {"a.txt": b"a", "b.txt": b"b"})
            return "alternate"

        self.bandizip.side_effect = bandizip
        self.run_files(source, embedded_scan_depth=1)
        self.bandizip.assert_called_once()
        self.assertTrue(self.bandizip.call_args.args[0].endswith(".AutoDecRecovered"))
        self.assertEqual(self.tree(self.base), {
            "cover": None, "cover/a.txt": b"a", "cover/b.txt": b"b"
        })

    def test_unopenable_original_does_not_use_bandizip_directly(self):
        source = self.source()
        self.run_files(source)
        self.bandizip.assert_not_called()
        self.assertEqual(self.tree(self.base), {"outer.zip": b"original archive"})
        self.assertEqual(self.recycled, [])

    def test_recent_success_password_is_reused_across_batches(self):
        first = self.source("first.zip")
        second = self.source("second.zip")
        self.archives.update({
            "first.zip": {"a.txt": b"a", "b.txt": b"b"},
            "second.zip": {"c.txt": b"c", "d.txt": b"d"},
        })
        extractor = self.run_files(first)
        self.book.passwords = {"different": 100}
        extractor.process_files([second])
        self.assertEqual(self.attempts, [("first.zip", "known"), ("second.zip", "known")])
        self.assertEqual(self.tree(self.base), {
            "first": None, "first/a.txt": b"a", "first/b.txt": b"b",
            "second": None, "second/c.txt": b"c", "second/d.txt": b"d"
        })


if __name__ == "__main__":
    unittest.main()
