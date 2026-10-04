"""Archive grouping contracts exercised against independent directory fixtures."""

from pathlib import Path
import tempfile
import unittest

from structure import (
    filter_non_primary_split_inputs,
    get_archive_base_name,
    group_archive_files,
    list_related_archive_parts,
)


class ArchiveStructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def files(self, *names):
        paths = []
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode("utf-8"))
            paths.append(str(path))
        return paths

    def test_numeric_primary_is_smallest_present_not_literal_first(self):
        self.files(
            "rar.part10.rar", "rar.part02.rar",
            "seven.7z.010", "seven.7z.002",
            "zip.zip.010", "zip.zip.002",
            "legacy.r10", "legacy.r02",
            "other.z10", "other.z02", "readme.txt",
        )
        (self.root / "folder.zip").mkdir()
        self.assertCountEqual(
            group_archive_files(str(self.root)),
            ["rar.part02.rar", "seven.7z.002", "zip.zip.002",
             "legacy.r02", "other.z02", "readme.txt"],
        )

    def test_rar_and_zip_companion_headers_take_priority(self):
        self.files("Pack.RAR", "Pack.r02", "Pack.r01",
                   "Other.ZIP", "Other.z02", "Other.z01")
        self.assertCountEqual(group_archive_files(str(self.root)),
                              ["Pack.RAR", "Other.ZIP"])

    def test_redirects_secondary_inputs_and_preserves_request_order(self):
        part10, first, single, part2, later = self.files(
            "pack.7z.010", "first.zip", "single.rar", "pack.7z.002", "later.zip"
        )
        self.assertEqual(
            filter_non_primary_split_inputs(
                [part10, first, part2, single, part10, later, first]
            ),
            [part2, first, single, later],
        )

    def test_identical_names_in_separate_directories_remain_separate(self):
        left2, right2, left1, right1 = self.files(
            "left/pack.zip.002", "right/pack.zip.002",
            "left/pack.zip.001", "right/pack.zip.001",
        )
        self.assertEqual(filter_non_primary_split_inputs([left2, right2]),
                         [left1, right1])

    def test_related_parts_do_not_capture_similar_names_or_other_formats(self):
        names = ["pack.part02.rar", "pack.part10.rar", "pack-extra.part01.rar",
                 "pack.7z.001", "pack.part02.rar.txt", "pack.zip"]
        paths = self.files(*names)
        self.assertCountEqual(list_related_archive_parts(paths[0]), paths[:2])

    def test_related_rar_and_zip_parts_include_their_headers(self):
        rar, r1, r2, zip_path, z1, z2, unrelated = self.files(
            "pack.rar", "pack.r00", "pack.r01", "pack.zip",
            "pack.z01", "pack.z02", "pack.txt",
        )
        for member in (rar, r1, r2):
            with self.subTest(member=Path(member).name):
                self.assertCountEqual(list_related_archive_parts(member), [rar, r1, r2])
        for member in (zip_path, z1, z2):
            with self.subTest(member=Path(member).name):
                self.assertCountEqual(list_related_archive_parts(member), [zip_path, z1, z2])
        self.assertEqual(list_related_archive_parts(unrelated), [unrelated])

    def test_numbered_zip_and_7z_parts_are_distinct_sets(self):
        seven1, seven2, zip1, zip2, unrelated = self.files(
            "pack.7z.001", "pack.7z.002", "pack.zip.001", "pack.zip.002", "pack.txt"
        )
        self.assertCountEqual(list_related_archive_parts(seven2), [seven1, seven2])
        self.assertCountEqual(list_related_archive_parts(zip2), [zip1, zip2])
        self.assertEqual(list_related_archive_parts(unrelated), [unrelated])

    def test_split_base_names_keep_dots_and_ignore_volume_suffixes(self):
        cases = {
            "my.pack.part02.RAR": "my.pack",
            "my.pack.r02": "my.pack",
            "my.pack.7Z.010": "my.pack",
            "my.pack.ZIP.002": "my.pack",
            "my.pack.z02": "my.pack",
            "my.pack.tar.gz": "my.pack.tar",
            "my.pack.zip": "my.pack",
            "extensionless": "extensionless",
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(get_archive_base_name(str(self.root / filename)), expected)


if __name__ == "__main__":
    unittest.main()
