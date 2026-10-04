"""Publication layouts and collision handling using real temporary trees."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from housekeeping import _normalize_path_for_compare
from output import publish_output


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / "stage"
        self.base = self.root / "output"
        self.trash = self.root / "trash"
        for directory in (self.stage, self.base, self.trash):
            directory.mkdir()
        self.recycled = []
        self.protected = set()
        self.reserved = set()
        self.enterContext(redirect_stdout(io.StringIO()))
        self.trash_mock = self.enterContext(patch("send2trash.send2trash", side_effect=self.recycle))

    @staticmethod
    def tree(root):
        return {
            path.relative_to(root).as_posix(): None if path.is_dir() else path.read_bytes()
            for path in root.rglob("*")
        }

    @staticmethod
    def put(root, name, data=b"payload"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def recycle(self, filename):
        path = Path(filename)
        self.assertTrue(path.resolve().is_relative_to(self.base.resolve()))
        self.assertTrue(path.exists())
        contents = self.tree(path) if path.is_dir() else path.read_bytes()
        self.recycled.append((path.name, contents))
        shutil.move(str(path), str(self.trash / str(len(self.recycled))))

    def publish(self, name="bundle", **options):
        settings = {
            "auto_flatten_single_file": True,
            "extract_to_base_folder": False,
            "source_archive_paths": self.reserved,
            "trash_on_success": True,
            "recycled_reserved_paths": self.protected,
        }
        settings.update(options)
        publish_output(str(self.stage), str(self.base), name, **settings)

    def reserve(self, name, data=b"source archive"):
        path = self.put(self.base, name, data)
        self.reserved.add(_normalize_path_for_compare(str(path)))
        return path

    def test_same_named_single_file_is_published_directly(self):
        self.put(self.stage, "BUNDLE.txt", b"contents")
        self.publish()
        self.assertEqual(self.tree(self.base), {"BUNDLE.txt": b"contents"})
        self.assertEqual(self.tree(self.stage), {})

    def test_prefix_rule_remains_active_when_single_file_flatten_is_disabled(self):
        self.put(self.stage, "bundle.txt", b"contents")
        self.publish(auto_flatten_single_file=False)
        self.assertEqual(self.tree(self.base), {"bundle.txt": b"contents"})

    def test_prefix_rule_flattens_multiple_case_insensitive_prefixed_files(self):
        self.put(self.stage, "Bundle-01.txt", b"first")
        self.put(self.stage, "bundle-cover.jpg", b"second")
        self.publish(auto_flatten_single_file=False)
        self.assertEqual(self.tree(self.base), {
            "Bundle-01.txt": b"first", "bundle-cover.jpg": b"second"
        })

    def test_one_nonprefixed_file_keeps_the_container_directory(self):
        self.put(self.stage, "bundle-01.txt", b"first")
        self.put(self.stage, "credits.txt", b"second")
        self.publish()
        self.assertEqual(self.tree(self.base), {
            "bundle": None, "bundle/bundle-01.txt": b"first", "bundle/credits.txt": b"second"
        })

    def test_prefixed_directory_is_not_treated_as_a_prefixed_file(self):
        self.put(self.stage, "bundle-dir/a.txt", b"inside")
        self.put(self.stage, "bundle.txt", b"outside")
        self.publish()
        self.assertEqual(self.tree(self.base), {
            "bundle": None, "bundle/bundle-dir": None,
            "bundle/bundle-dir/a.txt": b"inside", "bundle/bundle.txt": b"outside"
        })

    def test_explicit_base_folder_publication_moves_all_entries(self):
        self.put(self.stage, "unrelated.txt", b"outside")
        self.put(self.stage, "folder/a.txt", b"inside")
        self.publish(extract_to_base_folder=True)
        self.assertEqual(self.tree(self.base), {
            "unrelated.txt": b"outside", "folder": None, "folder/a.txt": b"inside"
        })

    def test_existing_files_receive_suffix_before_extension_without_overwrite(self):
        self.put(self.base, "bundle.txt", b"existing")
        self.put(self.base, "bundle~1.txt", b"also existing")
        self.put(self.stage, "bundle.txt", b"new")
        self.publish()
        self.assertEqual(self.tree(self.base), {
            "bundle.txt": b"existing", "bundle~1.txt": b"also existing", "bundle~2.txt": b"new"
        })
        self.assertEqual(self.recycled, [])

    def test_existing_directory_is_preserved_and_new_tree_receives_suffix(self):
        self.put(self.base, "bundle/old.txt", b"old")
        self.put(self.stage, "a.txt", b"new")
        self.publish()
        self.assertEqual(self.tree(self.base), {
            "bundle": None, "bundle/old.txt": b"old", "bundle~1": None, "bundle~1/a.txt": b"new"
        })
        self.assertEqual(self.recycled, [])

    def test_unrelated_file_at_desired_directory_name_is_preserved(self):
        self.put(self.base, "bundle", b"existing file")
        self.put(self.stage, "a.txt", b"new")
        self.publish()
        self.assertEqual(self.tree(self.base), {
            "bundle": b"existing file", "bundle~1": None, "bundle~1/a.txt": b"new"
        })
        self.assertEqual(self.recycled, [])

    def test_reserved_source_can_be_replaced_by_an_output_directory(self):
        source = self.reserve("bundle")
        self.put(self.stage, "a.txt", b"new")
        self.publish()
        self.assertEqual(self.tree(self.base), {"bundle": None, "bundle/a.txt": b"new"})
        self.assertEqual(self.recycled, [("bundle", b"source archive")])
        self.assertIn(_normalize_path_for_compare(str(source)), self.protected)

    def test_reserved_source_can_be_replaced_by_a_flattened_file(self):
        source = self.reserve("bundle.txt")
        self.put(self.stage, "bundle.txt", b"new")
        self.publish()
        self.assertEqual(self.tree(self.base), {"bundle.txt": b"new"})
        self.assertEqual(self.recycled, [("bundle.txt", b"source archive")])
        self.assertIn(_normalize_path_for_compare(str(source)), self.protected)

    def test_reserved_source_can_be_replaced_by_a_moved_directory(self):
        source = self.reserve("bundle.zip")
        self.put(self.stage, "bundle.zip/a.txt", b"new")
        self.publish(extract_to_base_folder=True)
        self.assertEqual(self.tree(self.base), {"bundle.zip": None, "bundle.zip/a.txt": b"new"})
        self.assertEqual(self.recycled, [("bundle.zip", b"source archive")])
        self.assertIn(_normalize_path_for_compare(str(source)), self.protected)

    def test_disabled_recycling_preserves_reserved_source_file(self):
        self.reserve("bundle.txt")
        self.put(self.stage, "bundle.txt", b"new")
        self.publish(trash_on_success=False)
        self.assertEqual(self.tree(self.base), {
            "bundle.txt": b"source archive", "bundle~1.txt": b"new"
        })
        self.assertEqual(self.recycled, [])
        self.assertEqual(self.protected, set())

    def test_failed_source_recycling_preserves_both_source_and_new_directory(self):
        self.reserve("bundle")
        self.put(self.stage, "a.txt", b"new")
        self.trash_mock.side_effect = OSError("Recycling unavailable")
        self.publish()
        self.assertEqual(self.tree(self.base), {
            "bundle": b"source archive", "bundle~1": None, "bundle~1/a.txt": b"new"
        })
        self.assertEqual(self.protected, set())

    def test_failed_source_recycling_preserves_both_source_and_new_file(self):
        self.reserve("bundle.txt")
        self.put(self.stage, "bundle.txt", b"new")
        self.trash_mock.side_effect = OSError("Recycling unavailable")
        self.publish()
        # A staged long filename may occupy ~1 through a filesystem short-name
        # alias. The observable contract is a unique suffixed file, not its index.
        tree = self.tree(self.base)
        self.assertEqual(tree.pop("bundle.txt"), b"source archive")
        self.assertEqual(len(tree), 1)
        name, contents = next(iter(tree.items()))
        self.assertRegex(name, r"^bundle~[1-9][0-9]*\.txt$")
        self.assertEqual(contents, b"new")
        self.assertEqual(self.tree(self.stage), {})
        self.assertEqual(self.protected, set())


if __name__ == "__main__":
    unittest.main()
