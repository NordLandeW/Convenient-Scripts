"""Offscreen Qt workflow tests; timeouts only guard blocked test infrastructure."""

import importlib.util
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["BG_FILTER_RENDER"] = "raster"

from PIL import Image
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("bg_filter", ROOT / "bg-filter.py")
browser_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = browser_module
spec.loader.exec_module(browser_module)
import scoring


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.browser = browser_module.ImageBrowser(desired_ratio=2)
        self.browser.target_pixels = 20000
        self.releases = []

    def tearDown(self):
        for event in self.releases:
            event.set()
        self.browser.close()
        self.browser._scan_executor.shutdown(wait=True, cancel_futures=True)
        self.browser.executor.shutdown(wait=True, cancel_futures=True)
        self.browser.prefetch_executor.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        self.browser.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def save(self, folder, name="image.png", color="red"):
        folder.mkdir(exist_ok=True)
        path = folder / name
        Image.new("RGB", (200, 100), color).save(path)
        return str(path)

    def wait_signal(self, signal, action, accept=lambda *args: True):
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        expired = []
        received = []

        def timeout():
            expired.append(True)
            loop.quit()

        def done(*args):
            if accept(*args):
                received.append(args)
                loop.quit()

        timer.timeout.connect(timeout)
        signal.connect(done, Qt.ConnectionType.QueuedConnection)
        try:
            timer.start(10000)
            action()
            if not received:
                loop.exec()
        finally:
            timer.stop()
            signal.disconnect(done)
        self.assertFalse(expired, "Timed out waiting for a semantic completion signal")
        self.assertTrue(received)
        return received[-1]

    def wait_scan(self, action):
        return self.wait_signal(
            self.browser.signals.scan_done, action,
            lambda generation, *_: generation == self.browser._scan_generation,
        )

    def flush_events(self):
        loop = QEventLoop()
        QTimer.singleShot(0, loop.quit)
        loop.exec()

    def test_background_scan_publishes_scores_on_gui_thread(self):
        path = self.save(self.root)
        threads = []
        self.browser.statusBar().messageChanged.connect(
            lambda *_: threads.append(threading.get_ident()), Qt.ConnectionType.DirectConnection
        )
        self.wait_scan(lambda: self.browser.load_folder(str(self.root)))
        self.assertFalse(self.browser._loading)
        self.assertEqual(self.browser.sorted_images, [path])
        self.assertGreater(self.browser.score_details[path].color, 0)
        self.assertTrue(threads)
        self.assertEqual(set(threads), {threading.get_ident()})

    def test_ui_dispatches_events_while_analysis_is_blocked(self):
        entered, release = Event(), Event()
        self.releases.append(release)
        original = browser_module.scan_folder

        def blocked(*args):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Test gate not released")
            return original(*args)

        self.save(self.root)
        with patch.object(browser_module, "scan_folder", side_effect=blocked):
            self.browser.load_folder(str(self.root))
            self.assertTrue(entered.wait(10))
            heartbeat = []
            QTimer.singleShot(0, lambda: heartbeat.append(True))
            self.flush_events()
            self.assertEqual(heartbeat, [True])
            self.assertTrue(self.browser._loading)
            self.assertFalse(release.is_set())
            self.wait_scan(release.set)

    def test_stale_scan_cannot_publish_over_new_folder(self):
        first, second = self.root / "first", self.root / "second"
        self.save(first, color="gray")
        second_path = self.save(second)
        old = scoring.scan_folder(str(first), 2, 20000, scoring.AnalysisCache(), Event(), lambda *_: None)
        entered_old, entered_new = Event(), Event()
        release_old, release_new = Event(), Event()
        self.releases.extend([release_old, release_new])
        original = browser_module.scan_folder

        def blocked(folder, *args):
            if folder == str(first):
                entered_old.set()
                if not release_old.wait(10):
                    raise TimeoutError("Old scan gate not released")
                return old  # Simulate a completion that races with cancellation.
            entered_new.set()
            if not release_new.wait(10):
                raise TimeoutError("New scan gate not released")
            return original(folder, *args)

        with patch.object(browser_module, "scan_folder", side_effect=blocked):
            self.browser.load_folder(str(first))
            self.assertTrue(entered_old.wait(10))
            self.browser.load_folder(str(second))
            release_old.set()
            self.assertTrue(entered_new.wait(10))
            self.flush_events()
            self.assertTrue(self.browser._loading)
            self.assertEqual(self.browser.sorted_images, [])
            self.wait_scan(release_new.set)
        self.assertEqual(self.browser.sorted_images, [second_path])

    def test_close_ignores_late_result(self):
        self.save(self.root)
        result = scoring.scan_folder(str(self.root), 2, 20000, scoring.AnalysisCache(), Event(), lambda *_: None)
        entered, release = Event(), Event()
        self.releases.append(release)

        def blocked(*_args):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Close test gate not released")
            return result

        with patch.object(browser_module, "scan_folder", side_effect=blocked):
            self.browser.load_folder(str(self.root))
            self.assertTrue(entered.wait(10))
            self.browser.close()
            self.wait_signal(self.browser.signals.scan_done, release.set)
        self.assertTrue(self.browser._scan_cancel.is_set())
        self.assertEqual(self.browser.sorted_images, [])

    def test_ratio_rescore_reuses_analysis_and_preserves_preview_snapshot(self):
        path = self.root / "crop.png"
        image = Image.new("RGB", (200, 100), "red")
        image.paste("gray", (50, 0, 150, 100))
        image.save(path)
        with patch.object(scoring, "read_analysis", wraps=scoring.read_analysis) as read:
            self.wait_scan(lambda: self.browser.load_folder(str(self.root)))
            self.browser.open_preview(str(path), 0)
            preview = self.browser.previews[-1]
            self.flush_events()
            old_details = preview.score_details[str(path)]
            preview._update_info_label()
            old_label = preview.info_label.text()
            self.assertIn("色彩分", old_label)
            self.browser.ratio_edit.setText("1:1")
            self.wait_scan(self.browser.apply_ratio)
            self.assertEqual(read.call_count, 1)
        self.assertGreater(old_details.color, 0)
        self.assertEqual(self.browser.score_details[str(path)].color, 0)
        self.assertEqual(preview.screen_ratio, 2)
        preview._update_info_label()
        self.assertEqual(preview.info_label.text(), old_label)
        self.browser.open_preview(str(path), 0)
        current = self.browser.previews[-1]
        self.assertEqual(current.screen_ratio, 1)
        self.assertEqual(current.score_details[str(path)].color, 0)

    def test_stale_thumbnails_are_not_restored_after_rescan(self):
        path = self.save(self.root)
        self.wait_scan(lambda: self.browser.load_folder(str(self.root)))
        old_key = (path, (100, 50), 1.0, self.browser._scan_generation)
        self.wait_scan(lambda: self.browser.load_folder(str(self.root)))
        image = Image.new("RGB", (100, 50), "red")
        self.browser._thumbnail_done(old_key, image)
        self.browser._prefetch_done(old_key, image)
        self.assertNotIn(old_key, self.browser.thumbnails)

    def test_empty_and_missing_folder_publish_nonmodal_status(self):
        self.wait_scan(lambda: self.browser.load_folder(str(self.root)))
        self.assertFalse(self.browser._loading)
        self.assertEqual(self.browser.sorted_images, [])
        self.assertIn("未找到", self.browser.statusBar().currentMessage())
        self.wait_scan(lambda: self.browser.load_folder(str(self.root / "missing")))
        self.assertFalse(self.browser._loading)
        self.assertIn("扫描失败", self.browser.statusBar().currentMessage())

    def test_grid_preview_and_navigation_use_completed_scores(self):
        self.save(self.root, "gray.png", "gray")
        self.save(self.root, "red.png", "red")
        self.browser.page_size = 1
        self.browser.resize(1400, 800)
        self.browser.show()
        self.wait_scan(lambda: self.browser.load_folder(str(self.root)))
        self.assertEqual(len(self.browser._cells), 1)
        self.assertEqual(Path(self.browser._cells[0].path).name, "red.png")
        self.browser.next_page()
        self.assertEqual(Path(self.browser._cells[0].path).name, "gray.png")
        self.browser.page_jump_edit.setText("1")
        self.browser.jump_to_page()
        self.assertEqual(Path(self.browser._cells[0].path).name, "red.png")
        self.browser.overlay_checkbox.setChecked(True)
        self.flush_events()
        self.assertFalse(self.browser.grab().isNull())


if __name__ == "__main__":
    unittest.main()
