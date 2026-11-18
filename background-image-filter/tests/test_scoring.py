"""Behavioral tests for ranking, sampled colors and bounded analysis."""

import math
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scoring


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = scoring.AnalysisCache()

    def save(self, name, color="gray", size=(200, 100), **options):
        path = self.root / name
        Image.new("RGB", size, color).save(path, **options)
        return str(path)

    def scan(self):
        return scoring.scan_folder(str(self.root), 2, 20000, self.cache, Event(), lambda *_: None)

    def test_colorful_beats_gray_at_equal_resolution_and_ratio(self):
        gray = self.save("gray.png")
        colorful = self.save("colorful.png", "red")
        result = self.scan()
        self.assertGreater(result.scores[colorful], result.scores[gray])
        self.assertEqual(result.details[gray].color, 0)
        self.assertGreater(result.details[colorful].color, 0)
        self.assertLess(result.details[colorful].color, 30)
        self.assertEqual(result.sorted_images[0], colorful)

    def test_grayscale_contrast_does_not_count_as_color(self):
        image = Image.new("RGB", (100, 100), "white")
        image.paste("black", (0, 0, 50, 100))
        self.assertEqual(scoring.colorfulness(image), 0)

    def test_opponent_color_variation_counts_beyond_solid_saturation(self):
        image = Image.new("RGB", (100, 100), "red")
        image.paste("green", (0, 0, 50, 100))
        self.assertGreater(scoring.colorfulness(image), scoring.colorfulness(Image.new("RGB", (100, 100), "red")))

    def test_colors_outside_visible_crop_do_not_contribute(self):
        image = Image.new("RGB", (200, 100), "red")
        image.paste("gray", (50, 0, 150, 100))
        path = self.root / "crop.png"
        image.save(path)
        analysis = self.cache.get(path)
        whole = scoring.score_analysis(analysis, 2, 20000)
        square = scoring.score_analysis(analysis, 1, 20000)
        self.assertGreater(whole.color, 0)
        self.assertEqual(square.color, 0)
        self.assertLess(square.resolution, whole.resolution)
        self.assertEqual(square.aspect, 100)

    def test_transparent_hidden_rgb_is_not_color(self):
        path = self.root / "transparent.png"
        image = Image.new("RGBA", (600, 300), (255, 0, 0, 0))
        image.paste((0, 0, 255, 0), (300, 0, 600, 300))
        image.save(path)
        analysis = self.cache.get(path)
        self.assertEqual(scoring.score_analysis(analysis, 2, 20000).color, 0)

    def test_palette_transparency_and_grayscale_modes(self):
        for mode in ("P", "L", "1", "LA"):
            with self.subTest(mode=mode):
                path = self.root / f"{mode}.png"
                image = Image.new(mode, (400, 200))
                if mode == "P":
                    image.putpalette([255, 0, 0] + [0] * 765)
                    image.info["transparency"] = 0
                image.save(path)
                analysis = self.cache.get(path)
                self.assertEqual(analysis.sample.mode, "RGB")
                self.assertLessEqual(max(analysis.sample.size), 256)
                self.assertEqual(scoring.score_analysis(analysis, 2, 20000).color, 0)

    def test_resolution_uses_remaining_pixels_and_saturates_smoothly(self):
        score = scoring.resolution_score
        self.assertAlmostEqual(score(400, 100, 1, 10000), score(100, 100, 1, 10000))
        low = score(50, 50, 1, 10000)
        exact = score(100, 100, 1, 10000)
        large = score(200, 200, 1, 10000)
        self.assertLess(low, exact)
        self.assertLess(exact, large)
        self.assertLess(large, 45)
        self.assertLess(large - exact, exact - low)
        below = score(1919, 1080, 16 / 9, 1920 * 1080)
        above = score(1920, 1080, 16 / 9, 1920 * 1080)
        self.assertLess(above - below, 0.1)
        self.assertGreaterEqual(low, -40)

    def test_rotated_image_and_target_have_equal_scores(self):
        image = Image.new("RGB", (200, 100), "red")
        a = scoring.Analysis((200, 100, 2, 1), image)
        b = scoring.Analysis((100, 200, 0.5, 1), image.transpose(Image.Transpose.ROTATE_90))
        self.assertEqual(scoring.score_analysis(a, 2, 20000), scoring.score_analysis(b, 0.5, 20000))

    def test_lossless_file_size_and_format_do_not_change_score(self):
        compact = self.save("compact.png", "red", compress_level=9)
        large = self.save("large.png", "red", compress_level=0)
        bitmap = self.save("bitmap.bmp", "red")
        self.assertNotEqual(os.path.getsize(compact), os.path.getsize(large))
        result = self.scan()
        self.assertEqual(result.scores[compact], result.scores[large])
        self.assertEqual(result.scores[compact], result.scores[bitmap])
        analysis = self.cache.get(compact)
        self.assertEqual(scoring.score_analysis(analysis, 2, 20000, ".png"), scoring.score_analysis(analysis, 2, 20000, ".jpg"))

    def test_unrelated_larger_file_does_not_change_existing_score(self):
        path = self.save("small.png", "red")
        before = self.scan().scores[path]
        self.save("large.bmp", "white", (600, 600))
        self.assertEqual(before, self.scan().scores[path])

    def test_gif_penalty_remains(self):
        path = self.save("image.gif", "red")
        score = self.scan().details[path]
        self.assertEqual(score.format_penalty, -1000)
        self.assertLess(score.total, 0)

    def test_ratio_changes_reuse_decoded_sample(self):
        path = self.save("cache.jpg", "red", (1600, 800))
        with patch.object(scoring, "read_analysis", wraps=scoring.read_analysis) as read:
            a = self.cache.get(path)
            scoring.score_analysis(a, 2, 20000)
            scoring.score_analysis(self.cache.get(path), 1, 20000)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(a.info[:2], (1600, 800))
        self.assertEqual(a.sample.size, (256, 128))

    def test_cache_invalidates_when_same_size_file_changes(self):
        path = self.save("same.bmp", "gray")
        old = self.cache.get(path)
        stamp = os.stat(path)
        self.save("same.bmp", "red")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000000))
        new = self.cache.get(path)
        self.assertEqual(old.info[3], new.info[3])
        self.assertEqual(scoring.colorfulness(old.sample), 0)
        self.assertGreater(scoring.colorfulness(new.sample), 0)

    def test_lru_budget_evicts_old_decodes(self):
        first = self.save("first.png", size=(8, 8))
        second = self.save("second.png", size=(8, 8))
        cache = scoring.AnalysisCache(max_bytes=8 * 8 * 4)
        with patch.object(scoring, "read_analysis", wraps=scoring.read_analysis) as read:
            cache.get(first)
            cache.get(second)
            cache.get(first)
        self.assertEqual(read.call_count, 3)

    def test_file_changed_during_decode_is_not_cached(self):
        path = self.save("changing.bmp")
        read = scoring.read_analysis

        def changing_read(path, size):
            result = read(path, size)
            stamp = os.stat(path)
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000000))
            return result

        with patch.object(scoring, "read_analysis", side_effect=changing_read):
            with self.assertRaises(OSError):
                self.cache.get(path)
        self.assertIsNotNone(self.cache.get(path))

    def test_bad_file_does_not_stop_scan_and_progress_completes(self):
        good = self.save("good.png")
        bad = self.root / "broken.png"
        bad.write_bytes(b"not an image")
        progress = []
        result = scoring.scan_folder(str(self.root), 2, 20000, self.cache, Event(), lambda *args: progress.append(args))
        self.assertEqual(result.sorted_images, [good, str(bad)])
        self.assertIn(str(bad), result.errors)
        self.assertLess(result.scores[str(bad)], result.scores[good])
        self.assertEqual(progress[0], (1, None))
        self.assertIn((0, 2), progress)
        self.assertEqual(progress[-1], (2, 2))

    def test_large_scan_reports_subpercent_progress_without_flooding(self):
        data = Path(self.save("first.bmp", size=(1, 1))).read_bytes()
        subdir = self.root / "nested"
        subdir.mkdir()
        for i in range(319):
            ((self.root if i % 2 else subdir) / f"{i}.bmp").write_bytes(data)
        expected = [os.path.join(root, name) for root, _, files in os.walk(self.root) for name in files]
        events = []
        now = 0.0

        def progress(count, total):
            nonlocal now
            events.append((count, total))
            if count == 1:
                now += 1.0  # One slow completion, then a burst within the same instant.

        with patch.object(scoring, "monotonic", lambda: now):
            result = scoring.scan_folder(str(self.root), 1, 1, self.cache, Event(), progress)
        self.assertEqual(events, [(1, None), (2, None), (0, 320), (1, 320), (2, 320), (320, 320)])
        self.assertEqual(result.sorted_images, expected)  # Equal scores keep discovery order.

    def test_empty_missing_and_pre_cancelled_folders(self):
        self.assertEqual(self.scan().images, [])
        with self.assertRaises(NotADirectoryError):
            scoring.scan_folder(str(self.root / "missing"), 2, 20000, self.cache, Event(), lambda *_: None)
        cancel = Event()
        cancel.set()
        self.assertIsNone(scoring.scan_folder(str(self.root), 2, 20000, self.cache, cancel, lambda *_: None))

    def test_cancellation_limits_outstanding_analysis(self):
        for index in range(12):
            self.save(f"{index}.png", size=(8, 8))
        entered, release, cancel = Event(), Event(), Event()
        lock = Lock()
        started = []
        cache = self.cache

        class BlockedCache:
            def get(inner, path):
                with lock:
                    started.append(path)
                    if len(started) == 4:
                        entered.set()
                if not release.wait(10):
                    raise TimeoutError("Test decode gate was not released")
                return cache.get(path)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(scoring.scan_folder, str(self.root), 2, 20000, BlockedCache(), cancel, lambda *_: None)
            try:
                self.assertTrue(entered.wait(10), "Analysis workers did not reach the gate")
                cancel.set()
            finally:
                release.set()
            self.assertIsNone(future.result(timeout=10))
        self.assertEqual(len(started), 4)

    def test_invalid_target_parameters_are_rejected(self):
        for value in (0, -1, math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    scoring.resolution_score(100, 100, value, 10000)
                with self.assertRaises(ValueError):
                    scoring.resolution_score(100, 100, 1, value)


if __name__ == "__main__":
    unittest.main()
