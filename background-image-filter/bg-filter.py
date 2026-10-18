#!/usr/bin/env python3
"""Background image browser (Qt edition).

Migrated from the Tk implementation (bg-filter-tk.py) without changing the
scoring rules or the feature set. Rendering runs on OpenGL where available:
the thumbnail grid and the preview canvas are QOpenGLWidgets, and interactive
zoom is scaled by the GPU while the Lanczos refinement is rendered in the
background. Set BG_FILTER_RENDER=raster to force software raster painting.
"""
import argparse
import ctypes
import math
import os
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from ctypes import c_void_p, memmove
from dataclasses import dataclass

from PIL import Image
from loguru import logger

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QImage,
    QKeySequence,
    QOffscreenSurface,
    QOpenGLContext,
    QPainter,
    QPixmap,
    QShortcut,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QStyleFactory,
    QVBoxLayout,
    QWidget,
)

try:
    from ctypes import windll
except ImportError:
    windll = None

# Highest-quality resampling filter Pillow provides. Used for both thumbnail
# downscaling and preview zooming; HAMMING softened fine detail noticeably.
RESAMPLE_FILTER = Image.Resampling.LANCZOS
THUMBNAIL_WORKERS = min(8, max(4, os.cpu_count() or 4))
PREFETCH_WORKERS = 2
GRID_COLUMNS = 3
GRID_MARGIN = 5
GRID_SPACING = 4


# ---------------------------------------------------------------------------
# Image helpers (unchanged from the Tk version)
# ---------------------------------------------------------------------------

def prepare_for_resampling(img):
    """Convert modes that cannot be interpolated correctly to RGB(A).

    Resizing a P-mode image directly interpolates palette indices instead of
    colors, producing broken, jagged lines. Palette transparency is kept by
    converting to RGBA.
    """
    if img.mode in ("RGBA", "LA", "PA") or (
        img.mode == "P" and "transparency" in img.info
    ):
        return img.convert("RGBA")
    if img.mode != "RGB":
        return img.convert("RGB")
    img.load()
    return img


def resize_to_fit(img, max_size):
    """Downscale to fit within max_size, preserving aspect ratio.

    Unlike Image.thumbnail(), this always resamples from the fully decoded
    source. thumbnail() may let the JPEG decoder subsample (draft mode), which
    softens fine detail before the final filter. A reducing gap of 3.0 is
    Pillow's documented near-fair two-step optimization: it avoids that decoder
    subsampling while cutting large-reduction cost substantially.
    """
    img = prepare_for_resampling(img)
    scale = min(max_size[0] / img.width, max_size[1] / img.height, 1.0)
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    if size == img.size:
        return img.copy()
    return img.resize(size, RESAMPLE_FILTER, reducing_gap=3.0)


def get_simplest_ratio(ratio_float):
    if ratio_float is None or ratio_float <= 0:
        return ""
    from fractions import Fraction

    f = Fraction(ratio_float).limit_denominator(100)
    return f"{f.numerator}:{f.denominator}"


def compute_score(img_path, target_ratio, max_area, max_size, preloaded_info=None):
    info = preloaded_info if preloaded_info is not None else get_image_info(img_path)
    if info is None:
        return -10000
    width, height, ratio, file_size = info

    score_aspect = aspect_ratio_score(ratio, target_ratio)

    ext = os.path.splitext(img_path)[1].lower()
    if ext == ".png":
        score_format = 20
    elif ext in [".jpg", ".jpeg"]:
        score_format = 10
    elif ext == ".gif":
        score_format = -1000
    else:
        score_format = 0

    if width * height >= 3840 * 2160:
        score_resolution = 45
    elif width * height >= 2560 * 1440:
        score_resolution = 40
    elif width * height >= 1920 * 1080:
        score_resolution = 20
    elif width * height >= 1280 * 720:
        score_resolution = 10
    else:
        score_resolution = -40

    score_filesize = 20 * (file_size / max_size) if max_size > 0 else 0

    return score_aspect + score_format + score_resolution + score_filesize


def aspect_ratio_score(image_ratio, desired_ratio):
    if desired_ratio <= 0 or image_ratio <= 0:
        return 0

    if image_ratio > desired_ratio:
        visible_ratio = desired_ratio / image_ratio
    else:
        visible_ratio = image_ratio / desired_ratio

    invisible_ratio = 1.0 - visible_ratio

    max_score = 200.0
    return max_score * (1.0 - invisible_ratio)


def get_image_info(filepath):
    try:
        with Image.open(filepath) as img:
            width, height = img.size
            ratio = width / height if height != 0 else 0
    except Exception:
        return None
    file_size = os.path.getsize(filepath)
    return width, height, ratio, file_size


def collect_images(folder):
    image_files = []
    allowed_ext = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff"}
    for root, dirs, files in os.walk(folder):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in allowed_ext:
                image_files.append(os.path.join(root, f))
    return image_files


def _console_print(*args, **kwargs):
    """print() that also survives pythonw.exe, where sys.stdout is None."""
    if sys.stdout is None:
        return
    print(*args, **kwargs)


def _print_progress(cur, total, prefix=""):
    if sys.stdout is None:
        return
    pct = cur * 100 // total
    sys.stdout.write(f"\r{prefix}{cur}/{total}  {pct}%")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Clipboard / external opening (unchanged behavior)
# ---------------------------------------------------------------------------

def copy_file_to_clipboard(filepath):
    if sys.platform.startswith("win"):
        try:
            import win32clipboard
            import win32con
        except ImportError:
            _console_print("需要安装 pywin32 以使用剪贴板功能。")
            return
        if windll is None:
            _console_print("windll 不可用，剪贴板功能无法使用。")
            return
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            dropfiles_header = struct.pack("IiiII", 20, 0, 0, 0, 1)
            file_list = (filepath + "\0").encode("utf-16le") + b"\0\0"
            data = dropfiles_header + file_list
            GHND = 0x0042
            windll.kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_ulong]
            windll.kernel32.GlobalAlloc.restype = c_void_p
            windll.kernel32.GlobalLock.argtypes = [c_void_p]
            windll.kernel32.GlobalLock.restype = c_void_p
            hGlobalMem = windll.kernel32.GlobalAlloc(GHND, len(data))
            if not hGlobalMem:
                win32clipboard.CloseClipboard()
                return
            pGlobalMem = windll.kernel32.GlobalLock(hGlobalMem)
            if not pGlobalMem:
                win32clipboard.CloseClipboard()
                return
            memmove(pGlobalMem, data, len(data))
            windll.kernel32.GlobalUnlock(ctypes.c_void_p(hGlobalMem))
            win32clipboard.SetClipboardData(win32con.CF_HDROP, hGlobalMem)
            win32clipboard.CloseClipboard()
            _console_print("已复制到剪贴板:", filepath)
        except Exception:
            logger.exception("复制文件到剪贴板失败")
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
        return
    if sys.platform.startswith("linux"):
        _copy_file_to_clipboard_linux(filepath)
        return
    _console_print("复制功能仅在 Windows / Linux 上受支持。")


def _copy_file_to_clipboard_linux(filepath):
    import shutil
    from pathlib import Path

    uri = Path(filepath).resolve().as_uri()
    data = f"{uri}\n"
    clipboard_commands = [
        ["wl-copy", "--type", "text/uri-list"],
        ["xclip", "-selection", "clipboard", "-t", "text/uri-list"],
        ["xsel", "--clipboard", "--input", "--mime-type", "text/uri-list"],
    ]
    available_commands = [cmd for cmd in clipboard_commands if shutil.which(cmd[0])]
    if not available_commands:
        _console_print("未检测到 wl-copy/xclip/xsel，无法复制文件到剪贴板。")
        return
    for cmd in available_commands:
        try:
            subprocess.run(cmd, input=data.encode("utf-8"), check=True)
            _console_print("已复制到剪贴板:", filepath)
            return
        except (subprocess.CalledProcessError, OSError):
            logger.exception("复制文件到剪贴板失败")
            continue
    _console_print("复制文件到剪贴板失败。")


def open_external_and_copy(img_path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(img_path)
            copy_file_to_clipboard(img_path)
        elif sys.platform.startswith("darwin"):
            subprocess.call(["open", img_path])
            _console_print("复制功能仅在 Windows / Linux 上受支持。")
        else:
            subprocess.call(["xdg-open", img_path])
            copy_file_to_clipboard(img_path)
    except Exception as e:
        _console_print("无法打开外部程序:", e)


# ---------------------------------------------------------------------------
# Qt image / render helpers
# ---------------------------------------------------------------------------

def physical_size(size, dpr):
    return (max(1, int(round(size[0] * dpr))), max(1, int(round(size[1] * dpr))))


def pil_to_qimage(img):
    """Convert a Pillow image to a QImage with tightly packed RGBA/RGB data."""
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.mode else "RGB")
    if img.mode == "RGBA":
        data = img.tobytes("raw", "RGBA")
        fmt = QImage.Format.Format_RGBA8888
        stride = img.width * 4
    else:
        data = img.tobytes("raw", "RGB")
        fmt = QImage.Format.Format_RGB888
        stride = img.width * 3
    # QImage does not own the buffer, so copy before the bytes go away.
    return QImage(data, img.width, img.height, stride, fmt).copy()


def pil_to_pixmap(img, dpr):
    pixmap = QPixmap.fromImage(pil_to_qimage(img))
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def apply_modern_style(app):
    """Use the native Windows 11 (WinUI) widget style when Qt provides it."""
    if "windows11" in QStyleFactory.keys():
        style = QStyleFactory.create("windows11")
        if style is not None:
            app.setStyle(style)
    else:
        app.setStyle("Fusion")

    families = set(QFontDatabase.families())
    for name in ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI"):
        if name in families:
            app.setFont(QFont(name, 10))
            break


_OPENGL_AVAILABLE = None


def opengl_available():
    """Probe the default OpenGL context; fall back to raster when unavailable."""
    global _OPENGL_AVAILABLE
    if _OPENGL_AVAILABLE is not None:
        return _OPENGL_AVAILABLE
    mode = os.environ.get("BG_FILTER_RENDER", "").strip().lower()
    if mode in ("raster", "software", "off", "0"):
        _OPENGL_AVAILABLE = False
        return False
    if QGuiApplication.platformName() in ("offscreen", "minimal"):
        _OPENGL_AVAILABLE = False
        return False
    ctx = QOpenGLContext()
    if not ctx.create():
        _OPENGL_AVAILABLE = False
        return False
    surface = QOffscreenSurface()
    surface.setFormat(ctx.format())
    surface.create()
    if not surface.isValid():
        _OPENGL_AVAILABLE = False
        return False
    ok = ctx.makeCurrent(surface)
    if ok:
        ctx.doneCurrent()
    _OPENGL_AVAILABLE = bool(ok)
    return _OPENGL_AVAILABLE


class WorkerSignals(QObject):
    """Queues worker completions onto the GUI thread."""

    thumbnail_done = Signal(object, object)          # key, PIL image | None
    prefetch_done = Signal(object, object)           # key, PIL image | None
    preview_done = Signal(int, object, object, object)  # generation, (w,h), dpr, PIL image
    neighbor_done = Signal(str, object)              # path, result tuple | None


# ---------------------------------------------------------------------------
# Thumbnail grid
# ---------------------------------------------------------------------------

@dataclass
class _Cell:
    path: str
    index: int
    rect: QRectF
    score_text: str
    info: object
    target_size: tuple
    dpr: float


class _GridRaster(QWidget):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMinimumSize(1, 1)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            self.browser.paint_grid(painter)
        finally:
            painter.end()

    def mousePressEvent(self, event):
        self.browser.grid_mouse_press(event.position(), event.button())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.browser.grid_resized()


class _GridOpenGL(QOpenGLWidget):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)
        self.setMinimumSize(1, 1)

    def paintGL(self):
        painter = QPainter(self)
        try:
            self.browser.paint_grid(painter)
        finally:
            painter.end()

    def mousePressEvent(self, event):
        self.browser.grid_mouse_press(event.position(), event.button())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.browser.grid_resized()


class ImageBrowser(QMainWindow):
    def __init__(self, folder=None, page_size=12, desired_ratio=None):
        super().__init__()
        self.setWindowTitle("图片浏览器")
        self.page_size = page_size
        self.folder = folder
        self.images = []
        self.sorted_images = []
        self.thumbnails = {}
        self.thumbnail_futures = {}
        self.prefetch_futures = {}
        self._last_thumbnail_size = None
        self.image_info_cache = {}
        self.score_cache = {}
        self.current_page = 0
        self.max_area = 0
        self.max_size = 0
        self._cells = []
        self._shown = False
        self._closed = False
        self.previews = []

        if desired_ratio is not None:
            self.screen_ratio = desired_ratio
        else:
            screen = QGuiApplication.primaryScreen()
            geo = screen.geometry() if screen is not None else None
            if geo and geo.height() > 0:
                self.screen_ratio = geo.width() / geo.height()
            else:
                self.screen_ratio = 16 / 9

        self.use_opengl = opengl_available()
        logger.info("渲染模式: {}", "OpenGL 硬件加速" if self.use_opengl else "软件光栅")
        self.placeholder_pixmap = QPixmap(10, 10)
        self.placeholder_pixmap.fill(QColor("gray"))

        self.executor = ThreadPoolExecutor(max_workers=THUMBNAIL_WORKERS)
        self.prefetch_executor = ThreadPoolExecutor(max_workers=PREFETCH_WORKERS)
        self.signals = WorkerSignals(self)
        self.signals.thumbnail_done.connect(self._thumbnail_done)
        self.signals.prefetch_done.connect(self._prefetch_done)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(100)
        self._resize_timer.timeout.connect(self.display_page)

        self._build_ui()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        if self.folder:
            self.load_folder(self.folder)

    # -- UI ---------------------------------------------------------------

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top = QWidget(central)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(5, 5, 5, 5)
        top_layout.setSpacing(5)

        self.folder_edit = QLineEdit(self.folder or "")
        self.folder_edit.setMinimumWidth(240)
        top_layout.addWidget(self.folder_edit)

        browse_button = QPushButton("选择文件夹")
        browse_button.clicked.connect(self.browse_folder)
        top_layout.addWidget(browse_button)

        load_button = QPushButton("加载")
        load_button.clicked.connect(lambda: self.load_folder(self.folder_edit.text()))
        top_layout.addWidget(load_button)

        top_layout.addWidget(QLabel("比例:"))
        self.ratio_edit = QLineEdit(get_simplest_ratio(self.screen_ratio))
        self.ratio_edit.setMaximumWidth(90)
        self.ratio_edit.returnPressed.connect(self.apply_ratio)
        top_layout.addWidget(self.ratio_edit)

        recalc_button = QPushButton("重新计算")
        recalc_button.clicked.connect(self.apply_ratio)
        top_layout.addWidget(recalc_button)

        prev_button = QPushButton("上一页")
        prev_button.clicked.connect(self.prev_page)
        top_layout.addWidget(prev_button)

        self.page_label = QLabel("第 0/0 页")
        top_layout.addWidget(self.page_label)

        next_button = QPushButton("下一页")
        next_button.clicked.connect(self.next_page)
        top_layout.addWidget(next_button)

        top_layout.addWidget(QLabel("跳至"))
        self.page_jump_edit = QLineEdit()
        self.page_jump_edit.setMaximumWidth(55)
        self.page_jump_edit.setEnabled(False)
        self.page_jump_edit.returnPressed.connect(self.jump_to_page)
        top_layout.addWidget(self.page_jump_edit)
        top_layout.addWidget(QLabel("页"))

        jump_button = QPushButton("跳转")
        jump_button.clicked.connect(self.jump_to_page)
        top_layout.addWidget(jump_button)

        self.overlay_checkbox = QCheckBox("比例遮挡")
        self.overlay_checkbox.toggled.connect(lambda _checked: self.display_page())
        top_layout.addWidget(self.overlay_checkbox)

        top_layout.addStretch(1)
        layout.addWidget(top)

        self.grid = _GridOpenGL(self) if self.use_opengl else _GridRaster(self)
        layout.addWidget(self.grid, 1)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shown:
            self._shown = True
            QTimer.singleShot(0, self.display_page)

    def closeEvent(self, event):
        self._closed = True
        self._resize_timer.stop()
        self._cancel_prefetch()
        future_list = list(self.thumbnail_futures.values())
        self.thumbnail_futures.clear()
        for future in future_list:
            future.cancel()
        for preview in list(self.previews):
            preview.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.prefetch_executor.shutdown(wait=False, cancel_futures=True)
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)

    def eventFilter(self, obj, event):
        try:
            if (
                event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)
                and isinstance(obj, QWidget)
                and obj.window() is self
            ):
                focus = QApplication.focusWidget()
                if not isinstance(focus, QLineEdit):
                    if event.key() == Qt.Key.Key_Left:
                        self.prev_page()
                    else:
                        self.next_page()
                    return True
        except RuntimeError:
            pass
        return super().eventFilter(obj, event)

    # -- Folder / scoring -------------------------------------------------

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择图片文件夹", self.folder_edit.text() or ""
        )
        if folder:
            self.folder_edit.setText(folder)
            self.load_folder(folder)

    def apply_ratio(self):
        ratio_str = self.ratio_edit.text().strip()
        try:
            if ":" in ratio_str:
                a, b = ratio_str.split(":")
                new_ratio = float(a) / float(b)
            else:
                new_ratio = float(ratio_str)

            self.screen_ratio = new_ratio
            self.ratio_edit.setText(get_simplest_ratio(self.screen_ratio))

            if self.folder:
                self.load_folder(self.folder)
                if self._shown:
                    self.display_page()
        except Exception:
            QMessageBox.critical(self, "错误", f"无效的比例格式: {ratio_str}")

    def load_folder(self, folder):
        self.folder = folder
        self.images = collect_images(folder)
        if not self.images:
            QMessageBox.information(self, "提示", "该文件夹中未找到图片！")
            self.sorted_images = []
            self.current_page = 0
            if self._shown:
                self.display_page()
            return

        total = len(self.images)
        self.image_info_cache.clear()
        self.score_cache.clear()
        self.max_area = 0
        self.max_size = 0
        max_workers = min(32, max(4, (os.cpu_count() or 1) * 2))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(get_image_info, path): path for path in self.images}
            for idx, fut in enumerate(as_completed(futures), 1):
                path = futures[fut]
                try:
                    info = fut.result()
                except Exception:
                    logger.exception("读取图片信息失败: {}", path)
                    info = None
                if info is not None:
                    self.image_info_cache[path] = info
                    w, h, _, sz = info
                    area = w * h
                    if area > self.max_area:
                        self.max_area = area
                    if sz > self.max_size:
                        self.max_size = sz
                _print_progress(idx, total, "正在读取 ")
        _console_print()

        score_dict = {}
        score_workers = min(32, max(4, (os.cpu_count() or 1) * 2))
        with ThreadPoolExecutor(max_workers=score_workers) as pool:
            futures = {
                pool.submit(
                    compute_score,
                    p,
                    self.screen_ratio,
                    self.max_area,
                    self.max_size,
                    self.image_info_cache.get(p),
                ): p
                for p in self.images
            }
            for idx, fut in enumerate(as_completed(futures), 1):
                p = futures[fut]
                try:
                    score_dict[p] = fut.result()
                except Exception:
                    logger.exception("计算得分失败: {}", p)
                    score_dict[p] = -10000
                _print_progress(idx, total, "正在评分 ")
        _console_print()

        self.score_cache = score_dict
        self.sorted_images = sorted(self.images, key=lambda x: score_dict[x], reverse=True)
        if self._shown and self.grid.width() > 1 and self.grid.height() > 1:
            self.display_page()

    def get_cached_image_info(self, img_path):
        info = self.image_info_cache.get(img_path)
        if info is None:
            info = get_image_info(img_path)
            if info is not None:
                self.image_info_cache[img_path] = info
        return info

    # -- Pages / grid -----------------------------------------------------

    def grid_resized(self):
        self._resize_timer.start()

    def display_page(self):
        if not self._shown or self._closed:
            return

        self._cells = []
        total_images = len(self.sorted_images)
        total_pages = (total_images + self.page_size - 1) // self.page_size if total_images else 0

        if total_pages == 0:
            self.page_label.setText("第 0/0 页")
            self._sync_page_entry(0)
            self._last_thumbnail_size = None
            self.grid.update()
            return

        if self.current_page >= total_pages:
            self.current_page = max(total_pages - 1, 0)

        self._sync_page_entry(total_pages)

        start_index = self.current_page * self.page_size
        end_index = start_index + self.page_size
        page_images = self.sorted_images[start_index:end_index]
        if not page_images:
            self.page_label.setText(f"第 {self.current_page + 1}/{total_pages} 页")
            self.grid.update()
            return

        columns = GRID_COLUMNS
        rows = max(1, math.ceil(len(page_images) / columns))
        width = self.grid.width()
        height = self.grid.height()
        if width <= 1 or height <= 1:
            target_size = (150, 150)
        else:
            avail_w = max(width - 2 * GRID_MARGIN - (columns - 1) * GRID_SPACING, columns)
            avail_h = max(height - 2 * GRID_MARGIN - (rows - 1) * GRID_SPACING, rows)
            target_size = (max(1, avail_w // columns), max(1, avail_h // rows))

        dpr = self.grid.devicePixelRatioF() or 1.0
        target_key = (target_size, dpr)
        if target_key != self._last_thumbnail_size:
            self._last_thumbnail_size = target_key
            self._cancel_prefetch()

        for idx, img_path in enumerate(page_images):
            row = idx // columns
            col = idx % columns
            x = GRID_MARGIN + col * (target_size[0] + GRID_SPACING)
            y = GRID_MARGIN + row * (target_size[1] + GRID_SPACING)
            rect = QRectF(x, y, target_size[0], target_size[1])

            self.get_thumbnail(img_path, target_size, dpr)
            cached_info = self.get_cached_image_info(img_path)
            total_score = self.score_cache.get(img_path)
            if total_score is None:
                total_score = compute_score(
                    img_path, self.screen_ratio, self.max_area, self.max_size, cached_info
                )
                self.score_cache[img_path] = total_score
            if cached_info is not None:
                _, _, ratio, _ = cached_info
                aspect_score = aspect_ratio_score(ratio, self.screen_ratio)
                score_text = f"{int(total_score)}\n({int(aspect_score)})"
            else:
                score_text = str(int(total_score))

            self._cells.append(
                _Cell(
                    path=img_path,
                    index=start_index + idx,
                    rect=rect,
                    score_text=score_text,
                    info=cached_info,
                    target_size=target_size,
                    dpr=dpr,
                )
            )

        self.page_label.setText(f"第 {self.current_page + 1}/{total_pages} 页")
        self.grid.update()
        self.prefetch_adjacent_pages(target_size, dpr)

    def paint_grid(self, painter):
        painter.fillRect(self.grid.rect(), self.palette().window().color())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        font = QFont(self.font())
        font.setPointSizeF(9.0)
        font.setBold(True)

        for cell in self._cells:
            rect = cell.rect
            pixmap = self.thumbnails.get(
                (cell.path, cell.target_size, cell.dpr), self.placeholder_pixmap
            )
            pw = pixmap.width() / (pixmap.devicePixelRatio() or 1.0)
            ph = pixmap.height() / (pixmap.devicePixelRatio() or 1.0)
            painter.drawPixmap(
                QPointF(rect.center().x() - pw / 2, rect.center().y() - ph / 2), pixmap
            )

            if self.overlay_checkbox.isChecked() and cell.info is not None:
                self._paint_grid_overlay(painter, rect, cell.info)

            painter.setFont(font)
            painter.setPen(QColor("green"))
            painter.drawText(
                rect.adjusted(0, 0, -4, -4),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                cell.score_text,
            )

    def _paint_grid_overlay(self, painter, rect, info):
        try:
            orig_width, orig_height, image_ratio, _ = info
            if orig_width <= 0 or orig_height <= 0:
                return
            scale = min(rect.width() / orig_width, rect.height() / orig_height)
            thumb_width = orig_width * scale
            thumb_height = orig_height * scale
            thumb_x1 = rect.left() + (rect.width() - thumb_width) / 2
            thumb_y1 = rect.top() + (rect.height() - thumb_height) / 2

            dim = QColor(0, 0, 0, 64)
            if image_ratio > self.screen_ratio:
                visible_width = thumb_width * (self.screen_ratio / image_ratio)
                visible_x1 = thumb_x1 + (thumb_width - visible_width) / 2
                visible_x2 = visible_x1 + visible_width
                if visible_x1 > thumb_x1:
                    painter.fillRect(
                        QRectF(thumb_x1, thumb_y1, visible_x1 - thumb_x1, thumb_height), dim
                    )
                if visible_x2 < thumb_x1 + thumb_width:
                    painter.fillRect(
                        QRectF(visible_x2, thumb_y1, thumb_x1 + thumb_width - visible_x2,
                               thumb_height), dim
                    )
            else:
                visible_height = thumb_height * (image_ratio / self.screen_ratio)
                visible_y1 = thumb_y1 + (thumb_height - visible_height) / 2
                visible_y2 = visible_y1 + visible_height
                if visible_y1 > thumb_y1:
                    painter.fillRect(
                        QRectF(thumb_x1, thumb_y1, thumb_width, visible_y1 - thumb_y1), dim
                    )
                if visible_y2 < thumb_y1 + thumb_height:
                    painter.fillRect(
                        QRectF(thumb_x1, visible_y2, thumb_width,
                               thumb_y1 + thumb_height - visible_y2), dim
                    )
        except Exception as e:
            logger.error("绘制缩略图遮罩失败: {}", e)

    def grid_mouse_press(self, pos, button):
        for cell in self._cells:
            if cell.rect.contains(pos):
                if button == Qt.MouseButton.LeftButton:
                    self.open_preview(cell.path, cell.index)
                elif button == Qt.MouseButton.RightButton:
                    open_external_and_copy(cell.path)
                return

    def _sync_page_entry(self, total_pages):
        if total_pages <= 0:
            self.page_jump_edit.clear()
            self.page_jump_edit.setEnabled(False)
            return
        self.page_jump_edit.setEnabled(True)
        if self.page_jump_edit.hasFocus():
            return
        self.page_jump_edit.setText(str(self.current_page + 1))

    def jump_to_page(self):
        total_pages = (
            (len(self.sorted_images) + self.page_size - 1) // self.page_size
            if self.sorted_images
            else 0
        )
        if total_pages == 0:
            return
        text = self.page_jump_edit.text().strip()
        try:
            page = int(text)
        except ValueError:
            QMessageBox.critical(self, "错误", f"无效的页码: {text}")
            self.page_jump_edit.setText(str(self.current_page + 1))
            return
        page = max(1, min(total_pages, page))
        self.current_page = page - 1
        self.page_jump_edit.setText(str(page))
        self.display_page()

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.display_page()

    def next_page(self):
        total_pages = (
            (len(self.sorted_images) + self.page_size - 1) // self.page_size
            if self.sorted_images
            else 0
        )
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self.display_page()

    # -- Thumbnail cache / prefetch ---------------------------------------

    def get_thumbnail(self, img_path, target_size, dpr):
        key = (img_path, target_size, dpr)
        if key in self.thumbnails:
            return self.thumbnails[key]
        if key in self.thumbnail_futures:
            return None
        prefetch_future = self.prefetch_futures.pop(key, None)
        if prefetch_future is not None:
            if not prefetch_future.cancelled():
                self.thumbnail_futures[key] = prefetch_future
                prefetch_future.add_done_callback(
                    lambda fut, key=key: self._thumbnail_finished(key, fut)
                )
                return None
        future = self.executor.submit(
            self.generate_thumbnail_image, img_path, target_size, dpr
        )
        self.thumbnail_futures[key] = future
        future.add_done_callback(lambda fut, key=key: self._thumbnail_finished(key, fut))
        return None

    def generate_thumbnail_image(self, img_path, target_size, dpr):
        try:
            with Image.open(img_path) as img:
                return resize_to_fit(img, physical_size(target_size, dpr))
        except Exception as e:
            logger.error("生成缩略图失败 {}: {}", img_path, e)
            return None

    def _thumbnail_finished(self, key, future):
        try:
            pil_image = future.result()
        except Exception:
            pil_image = None
        try:
            self.signals.thumbnail_done.emit(key, pil_image)
        except RuntimeError:
            pass

    def _thumbnail_done(self, key, pil_image):
        if self._closed:
            return
        self.thumbnail_futures.pop(key, None)
        if key not in self.thumbnails and pil_image is not None:
            try:
                self.thumbnails[key] = pil_to_pixmap(pil_image, key[2])
            except Exception as e:
                logger.error("转换 Pixmap 失败: {}", e)
        self.grid.update()

    def _cancel_prefetch(self):
        futures = list(self.prefetch_futures.values())
        self.prefetch_futures.clear()
        for future in futures:
            future.cancel()

    def prefetch_adjacent_pages(self, target_size, dpr):
        total_pages = (
            (len(self.sorted_images) + self.page_size - 1) // self.page_size
            if self.sorted_images
            else 0
        )
        if total_pages == 0:
            return

        desired_paths = set()
        for page in (self.current_page - 1, self.current_page + 1):
            if 0 <= page < total_pages:
                start = page * self.page_size
                desired_paths.update(self.sorted_images[start:start + self.page_size])

        for key, future in list(self.prefetch_futures.items()):
            if key[1:] != (target_size, dpr) or key[0] not in desired_paths:
                future.cancel()
                self.prefetch_futures.pop(key, None)

        for img_path in desired_paths:
            self.prefetch_thumbnail(img_path, target_size, dpr)

    def prefetch_thumbnail(self, img_path, target_size, dpr):
        key = (img_path, target_size, dpr)
        if key in self.thumbnails or key in self.thumbnail_futures or key in self.prefetch_futures:
            return
        future = self.prefetch_executor.submit(
            self.generate_thumbnail_image, img_path, target_size, dpr
        )
        self.prefetch_futures[key] = future
        future.add_done_callback(lambda fut, key=key: self._prefetch_finished(key, fut))

    def _prefetch_finished(self, key, future):
        try:
            pil_image = future.result()
        except Exception:
            pil_image = None
        try:
            self.signals.prefetch_done.emit(key, pil_image)
        except RuntimeError:
            pass

    def _prefetch_done(self, key, pil_image):
        if self._closed:
            return
        self.prefetch_futures.pop(key, None)
        if key in self.thumbnails or pil_image is None:
            return
        try:
            self.thumbnails[key] = pil_to_pixmap(pil_image, key[2])
        except Exception as e:
            logger.error("转换 Pixmap 失败: {}", e)

    # -- Preview ----------------------------------------------------------

    def open_preview(self, img_path, index):
        preview = PreviewWindow(
            self,
            self.sorted_images,
            index,
            self.screen_ratio,
            self.max_area,
            self.max_size,
            self.image_info_cache,
            self.score_cache,
        )
        if getattr(preview, "_init_failed", False):
            return
        self.previews.append(preview)
        preview.destroyed.connect(lambda *_: self._forget_preview(preview))
        preview.show()
        preview.raise_()
        preview.activateWindow()

    def _forget_preview(self, preview):
        if preview in self.previews:
            self.previews.remove(preview)


# ---------------------------------------------------------------------------
# Preview window
# ---------------------------------------------------------------------------

class _PreviewCanvasRaster(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window_ref = window
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(False)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            self.window_ref.paint_canvas(painter)
        finally:
            painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.window_ref.canvas_resized()

    def enterEvent(self, event):
        self.setFocus()
        super().enterEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window_ref.start_drag(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.window_ref.do_drag(event.position())

    def wheelEvent(self, event):
        self.window_ref.on_mousewheel(event)


class _PreviewCanvasOpenGL(QOpenGLWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window_ref = window
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)

    def paintGL(self):
        painter = QPainter(self)
        try:
            self.window_ref.paint_canvas(painter)
        finally:
            painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.window_ref.canvas_resized()

    def enterEvent(self, event):
        self.setFocus()
        super().enterEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window_ref.start_drag(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.window_ref.do_drag(event.position())

    def wheelEvent(self, event):
        self.window_ref.on_mousewheel(event)


class PreviewWindow(QWidget):
    def __init__(self, parent, image_list, index, screen_ratio, max_area, max_size,
                 info_cache, score_cache):
        super().__init__(parent, Qt.WindowType.Window)
        self._init_failed = False
        self._closed = False
        self._shown = False
        self.screen_ratio = screen_ratio
        self.max_area = max_area
        self.max_size = max_size
        self.image_list = image_list
        self.index = index
        self.img_path = self.image_list[self.index]
        self.info_cache = info_cache
        self.score_cache = score_cache

        try:
            with Image.open(self.img_path) as img:
                self.original_image = prepare_for_resampling(img)
        except Exception as e:
            logger.error("无法打开图片: {}\n{}", self.img_path, e)
            self._init_failed = True
            self.deleteLater()
            return

        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(f"预览: {os.path.basename(self.img_path)}")
        screen = parent.screen() if parent is not None else QGuiApplication.primaryScreen()
        geo = screen.geometry() if screen is not None else None
        if geo is not None:
            w = int(geo.width() * 0.75)
            h = int(geo.height() * 0.75)
            self.resize(w, h)
            self.move(geo.center() - QPoint(w // 2, h // 2))

        self._dpr = self.devicePixelRatioF() or 1.0
        self._preview_executor = ThreadPoolExecutor(max_workers=1)
        self._render_generation = 0
        self._pending_render_size = None
        self.photo = None
        self._display_size = None
        self._rendered_size = None
        self.visible_size = None
        self._neighbor_executor = ThreadPoolExecutor(max_workers=1)
        self._neighbor_cache = {}
        self._neighbor_futures = {}
        self._prefetch_canvas_key = None
        self._canvas_resize_timer = QTimer(self)
        self._canvas_resize_timer.setSingleShot(True)
        self._canvas_resize_timer.setInterval(100)
        self._canvas_resize_timer.timeout.connect(self._delayed_display_image)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(60)
        self._render_timer.timeout.connect(self._start_preview_render)

        self.zoom_level = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 3.0
        self._ignore_zoom_callback = False
        self._drag_start = QPointF()
        self.view_offset_x = 0.0
        self.view_offset_y = 0.0
        self.max_offset_x = 0.0
        self.max_offset_y = 0.0
        self._cached_canvas_size = None

        self.signals = WorkerSignals(self)
        self.signals.preview_done.connect(self._apply_preview_render)
        self.signals.neighbor_done.connect(self._neighbor_done)

        self._build_ui()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.canvas = _PreviewCanvasOpenGL(self) if opengl_available() else _PreviewCanvasRaster(self)
        outer.addWidget(self.canvas, 1)

        slider_row = QHBoxLayout()
        slider_row.setContentsMargins(5, 5, 5, 0)
        slider_row.addWidget(QLabel("额外缩放"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, 30)
        self.slider.setValue(10)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.valueChanged.connect(self.on_slider_changed)
        slider_row.addWidget(self.slider, 1)
        self.zoom_value_label = QLabel("1.0×")
        self.zoom_value_label.setMinimumWidth(42)
        slider_row.addWidget(self.zoom_value_label)
        outer.addLayout(slider_row)

        self.overlay_checkbox = QCheckBox("显示屏幕比例遮挡")
        self.overlay_checkbox.toggled.connect(lambda _checked: self.display_image())
        checkbox_row = QHBoxLayout()
        checkbox_row.setContentsMargins(5, 2, 5, 2)
        checkbox_row.addWidget(self.overlay_checkbox)
        checkbox_row.addStretch(1)
        outer.addLayout(checkbox_row)

        self.info_label = QLabel("")
        info_row = QHBoxLayout()
        info_row.setContentsMargins(5, 2, 5, 2)
        info_row.addWidget(self.info_label)
        info_row.addStretch(1)
        outer.addLayout(info_row)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(5, 5, 5, 5)
        prev_button = QPushButton("上一张")
        prev_button.clicked.connect(self.prev_image)
        button_row.addWidget(prev_button)
        next_button = QPushButton("下一张")
        next_button.clicked.connect(self.next_image)
        button_row.addWidget(next_button)
        button_row.addStretch(1)
        ext_button = QPushButton("外部打开并复制")
        ext_button.clicked.connect(self.open_external_and_copy)
        button_row.addWidget(ext_button)
        outer.addLayout(button_row)

        self._copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self)
        self._copy_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._copy_shortcut.activated.connect(self.copy_current_file)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shown:
            self._shown = True
            QTimer.singleShot(0, self._initial_display)

    def _initial_display(self):
        self.display_image()
        self._prefetch_neighbors()

    def eventFilter(self, obj, event):
        try:
            if (
                event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)
                and isinstance(obj, QWidget)
                and obj.window() is self
            ):
                focus = QApplication.focusWidget()
                if not isinstance(focus, QSlider):
                    if event.key() == Qt.Key.Key_Left:
                        self.prev_image()
                    else:
                        self.next_image()
                    return True
        except RuntimeError:
            pass
        return super().eventFilter(obj, event)

    def canvas_resized(self):
        self._canvas_resize_timer.start()

    def _delayed_display_image(self):
        self.display_image()
        self._prefetch_neighbors()

    # -- Painting ---------------------------------------------------------

    def paint_canvas(self, painter):
        painter.fillRect(self.canvas.rect(), QColor("#1f1f1f"))
        if self.photo is None or self._display_size is None:
            return

        dw, dh = self._display_size
        cx = self.canvas.width() / 2 + self.view_offset_x
        cy = self.canvas.height() / 2 + self.view_offset_y

        pixmap = self.photo
        pw = pixmap.width() / (pixmap.devicePixelRatio() or 1.0)
        ph = pixmap.height() / (pixmap.devicePixelRatio() or 1.0)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.translate(cx, cy)
        if abs(pw - dw) > 0.01 or abs(ph - dh) > 0.01:
            painter.scale(dw / pw, dh / ph)
        painter.drawPixmap(QPointF(-pw / 2, -ph / 2), pixmap)
        painter.restore()

        if self.overlay_checkbox.isChecked() and self.visible_size is not None:
            self._paint_preview_overlay(painter, cx, cy, dw, dh)

    def _paint_preview_overlay(self, painter, cx, cy, dw, dh):
        try:
            visible_width, visible_height = self.visible_size
            img_x1 = cx - dw / 2
            img_y1 = cy - dh / 2
            img_x2 = cx + dw / 2
            img_y2 = cy + dh / 2

            visible_x1 = max(cx - visible_width / 2, img_x1)
            visible_y1 = max(cy - visible_height / 2, img_y1)
            visible_x2 = min(cx + visible_width / 2, img_x2)
            visible_y2 = min(cy + visible_height / 2, img_y2)

            fill = QColor(0, 0, 0, 128)
            painter.fillRect(QRectF(img_x1, img_y1, img_x2 - img_x1, visible_y1 - img_y1), fill)
            painter.fillRect(QRectF(img_x1, visible_y2, img_x2 - img_x1, img_y2 - visible_y2), fill)
            painter.fillRect(QRectF(img_x1, visible_y1, visible_x1 - img_x1, visible_y2 - visible_y1), fill)
            painter.fillRect(QRectF(visible_x2, visible_y1, img_x2 - visible_x2, visible_y2 - visible_y1), fill)
        except Exception as e:
            logger.error("绘制遮罩失败: {}", e)

    def display_image(self):
        if self._closed or not self._shown:
            return
        canvas_width = self.canvas.width()
        canvas_height = self.canvas.height()
        if canvas_width <= 1 or canvas_height <= 1:
            return
        try:
            orig_width, orig_height = self.original_image.size
        except Exception as e:
            logger.error("获取图片尺寸失败: {}", e)
            return
        if orig_width <= 0 or orig_height <= 0:
            return

        dpr = self.devicePixelRatioF() or 1.0
        if dpr != self._dpr:
            self._dpr = dpr
            self._cancel_pending_render()
            self.photo = None
            self._display_size = None
            self._rendered_size = None
            self._neighbor_cache.clear()
            self._cancel_neighbor_futures()
            self._prefetch_canvas_key = None

        base_zoom = min(canvas_width / orig_width, canvas_height / orig_height)
        effective_zoom = base_zoom * self.zoom_level
        new_size = (max(int(orig_width * effective_zoom), 1),
                    max(int(orig_height * effective_zoom), 1))

        if self.photo is None:
            self._cancel_pending_render()
            try:
                resized = self.original_image.resize(
                    physical_size(new_size, dpr), RESAMPLE_FILTER
                )
            except Exception as e:
                logger.error("图片调整大小失败: {}", e)
                return
            self._rendered_size = new_size
            self._set_display_image(resized, new_size, dpr)
        elif new_size == self._rendered_size:
            self._cancel_pending_render()
            self._display_size = new_size
        else:
            # The pixmap already on screen is scaled by the GPU for an
            # immediate interim; the Lanczos render replaces it when ready.
            self._display_size = new_size
            self._schedule_preview_render(new_size)

        self._refresh_displayed_image()
        self._update_info_label()

    def _set_display_image(self, pil_image, size, dpr):
        try:
            self.photo = pil_to_pixmap(pil_image, dpr)
        except Exception as e:
            logger.error("转换 Pixmap 失败: {}", e)
            return
        self._display_size = size

    def _refresh_displayed_image(self):
        if self.photo is None or self._display_size is None:
            return
        canvas_width = self.canvas.width()
        canvas_height = self.canvas.height()
        if canvas_width <= 1 or canvas_height <= 1:
            return
        displayed_width, displayed_height = self._display_size
        self.max_offset_x = max((displayed_width - canvas_width) / 2, 0)
        self.max_offset_y = max((displayed_height - canvas_height) / 2, 0)
        self._cached_canvas_size = (canvas_width, canvas_height)

        visible_size = None
        if self.overlay_checkbox.isChecked():
            try:
                orig_width, orig_height = self.original_image.size
                image_ratio = orig_width / orig_height
            except ZeroDivisionError:
                image_ratio = 0
            if image_ratio > 0:
                if image_ratio > self.screen_ratio:
                    visible_width = displayed_height * self.screen_ratio
                    visible_height = displayed_height
                else:
                    visible_width = displayed_width
                    visible_height = displayed_width / self.screen_ratio
                visible_width = min(visible_width, displayed_width)
                visible_height = min(visible_height, displayed_height)
                visible_size = (visible_width, visible_height)
        self.visible_size = visible_size

        self._clamp_offsets()
        self.canvas.update()

    def _clamp_offsets(self):
        self.view_offset_x = max(-self.max_offset_x, min(self.max_offset_x, self.view_offset_x))
        self.view_offset_y = max(-self.max_offset_y, min(self.max_offset_y, self.view_offset_y))

    def _update_info_label(self):
        self.setWindowTitle(f"预览: {os.path.basename(self.img_path)}")
        info = self.get_cached_image_info(self.img_path)
        if info is not None:
            width, height, ratio, file_size = info
            total_score = self.get_cached_score(self.img_path, info)
            aspect_score = aspect_ratio_score(ratio, self.screen_ratio)
            self.info_label.setText(
                f"分辨率: {width}x{height}, 大小: {file_size} 字节, "
                f"总分: {total_score:.1f}, 比例分: {aspect_score:.1f}"
            )

    # -- Async high-quality rendering -------------------------------------

    def _cancel_pending_render(self):
        self._render_timer.stop()
        self._pending_render_size = None
        self._render_generation += 1

    def _schedule_preview_render(self, size):
        if self._pending_render_size != size:
            self._pending_render_size = size
            self._render_generation += 1
        self._render_timer.start()

    def _start_preview_render(self):
        if self._closed:
            return
        size = self._pending_render_size
        if size is None or self._rendered_size == size:
            return
        self._pending_render_size = None
        self._render_generation += 1
        generation = self._render_generation
        image = self.original_image
        dpr = self._dpr
        future = self._preview_executor.submit(
            self._render_preview_image, generation, image, size, dpr
        )
        future.add_done_callback(self._preview_render_finished)

    def _render_preview_image(self, generation, image, size, dpr):
        if generation != self._render_generation:
            return None
        try:
            resized = image.resize(physical_size(size, dpr), RESAMPLE_FILTER)
        except Exception:
            logger.exception("预览图渲染失败")
            return None
        return generation, size, dpr, resized

    def _preview_render_finished(self, future):
        try:
            result = future.result()
        except Exception:
            logger.exception("预览图渲染失败")
            return
        if result is None:
            return
        generation, size, dpr, resized = result
        try:
            self.signals.preview_done.emit(generation, size, dpr, resized)
        except RuntimeError:
            pass

    def _apply_preview_render(self, generation, size, dpr, resized):
        if self._closed or generation != self._render_generation:
            return
        if abs(dpr - self._dpr) > 1e-6:
            return
        self._rendered_size = size
        self._set_display_image(resized, size, dpr)
        self._refresh_displayed_image()
        self._update_info_label()

    # -- Zoom / pan -------------------------------------------------------

    def on_slider_changed(self, value):
        if self._ignore_zoom_callback:
            return
        self.change_zoom(value / 10.0, update_slider=False)

    def _set_slider_zoom(self, zoom):
        self._ignore_zoom_callback = True
        try:
            self.slider.setValue(int(round(zoom * 10)))
        finally:
            self._ignore_zoom_callback = False
        self.zoom_value_label.setText(f"{zoom:.1f}×")

    def change_zoom(self, new_zoom, focal_point=None, update_slider=False, reset_offsets=False):
        new_zoom = max(self.min_zoom, min(self.max_zoom, new_zoom))
        try:
            orig_width, orig_height = self.original_image.size
        except Exception as e:
            logger.error("获取图片尺寸失败: {}", e)
            return

        if reset_offsets:
            self.view_offset_x = 0
            self.view_offset_y = 0

        canvas_width = self.canvas.width()
        canvas_height = self.canvas.height()
        if canvas_width > 1 and canvas_height > 1:
            base_zoom = min(canvas_width / orig_width, canvas_height / orig_height)
            new_effective_zoom = base_zoom * new_zoom
            new_width = orig_width * new_effective_zoom
            new_height = orig_height * new_effective_zoom
            if self.photo is not None and self._display_size is not None:
                # Scale from the resolution actually on screen, not from a
                # newer zoom whose render has not completed yet.
                old_width, old_height = self._display_size
            else:
                old_effective_zoom = base_zoom * self.zoom_level
                old_width = orig_width * old_effective_zoom
                old_height = orig_height * old_effective_zoom

            if not reset_offsets and old_width > 0 and old_height > 0:
                image_center_x = canvas_width / 2 + self.view_offset_x
                image_center_y = canvas_height / 2 + self.view_offset_y
                if focal_point is not None:
                    rel_x = focal_point[0] - image_center_x
                    rel_y = focal_point[1] - image_center_y
                    ratio_x = rel_x / old_width
                    ratio_y = rel_y / old_height
                    new_center_x = focal_point[0] - ratio_x * new_width
                    new_center_y = focal_point[1] - ratio_y * new_height
                    self.view_offset_x = new_center_x - canvas_width / 2
                    self.view_offset_y = new_center_y - canvas_height / 2
                else:
                    scale_x = new_width / old_width if old_width != 0 else 1
                    scale_y = new_height / old_height if old_height != 0 else 1
                    self.view_offset_x *= scale_x
                    self.view_offset_y *= scale_y

        self.zoom_level = new_zoom
        if update_slider:
            self._set_slider_zoom(new_zoom)
        else:
            self.zoom_value_label.setText(f"{new_zoom:.1f}×")
        self.display_image()

    def on_mousewheel(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.pixelDelta().y()
        if delta == 0:
            return
        step = 1.1 if delta > 0 else 1 / 1.1
        new_zoom = self.zoom_level * step
        pos = event.position()
        self.change_zoom(new_zoom, focal_point=(pos.x(), pos.y()), update_slider=True)
        event.accept()

    def start_drag(self, pos):
        self.canvas.setFocus()
        self._drag_start = pos

    def do_drag(self, pos):
        dx = pos.x() - self._drag_start.x()
        dy = pos.y() - self._drag_start.y()
        self._drag_start = pos
        self.view_offset_x += dx
        self.view_offset_y += dy
        self._clamp_offsets()
        self.canvas.update()

    # -- Image navigation / prefetch --------------------------------------

    def prev_image(self):
        if self.index > 0:
            self.index -= 1
            self.load_image()

    def next_image(self):
        if self.index < len(self.image_list) - 1:
            self.index += 1
            self.load_image()

    def _neighbor_paths(self):
        paths = []
        if self.index > 0:
            paths.append(self.image_list[self.index - 1])
        if self.index < len(self.image_list) - 1:
            paths.append(self.image_list[self.index + 1])
        return paths

    def _cancel_neighbor_futures(self):
        futures = list(self._neighbor_futures.values())
        self._neighbor_futures.clear()
        for future in futures:
            future.cancel()

    def _prefetch_neighbors(self):
        if self._closed or not self._shown:
            return
        canvas_key = (self.canvas.width(), self.canvas.height(), self._dpr)
        if canvas_key[0] <= 1 or canvas_key[1] <= 1:
            return
        if self._prefetch_canvas_key != canvas_key:
            self._prefetch_canvas_key = canvas_key
            self._cancel_neighbor_futures()
            self._neighbor_cache.clear()

        paths = self._neighbor_paths()
        for path in list(self._neighbor_cache):
            if path not in paths:
                self._neighbor_cache.pop(path, None)
        for path, future in list(self._neighbor_futures.items()):
            if path not in paths:
                future.cancel()
                self._neighbor_futures.pop(path, None)

        for path in paths:
            if path in self._neighbor_cache or path in self._neighbor_futures:
                continue
            future = self._neighbor_executor.submit(
                self._render_neighbor, path, (canvas_key[0], canvas_key[1]), self._dpr
            )
            self._neighbor_futures[path] = future
            future.add_done_callback(
                lambda fut, path=path: self._neighbor_finished(path, fut)
            )

    def _render_neighbor(self, path, canvas_size, dpr):
        try:
            with Image.open(path) as img:
                original = prepare_for_resampling(img)
            canvas_width, canvas_height = canvas_size
            base_zoom = min(canvas_width / original.width, canvas_height / original.height)
            fit_size = (max(int(original.width * base_zoom), 1),
                        max(int(original.height * base_zoom), 1))
            fit_image = original.resize(physical_size(fit_size, dpr), RESAMPLE_FILTER)
            return path, original, fit_size, fit_image, dpr
        except Exception:
            logger.exception("预渲染相邻图片失败: {}", path)
            return None

    def _neighbor_finished(self, path, future):
        try:
            result = future.result()
        except Exception:
            return
        try:
            self.signals.neighbor_done.emit(path, result)
        except RuntimeError:
            pass

    def _neighbor_done(self, path, result):
        self._neighbor_futures.pop(path, None)
        if self._closed or result is None or path not in self._neighbor_paths():
            return
        path, original, fit_size, fit_image, dpr = result
        if abs(dpr - self._dpr) > 1e-6:
            return
        canvas_width = self.canvas.width()
        canvas_height = self.canvas.height()
        if canvas_width <= 1 or canvas_height <= 1:
            return
        base_zoom = min(canvas_width / original.width, canvas_height / original.height)
        expected_size = (max(int(original.width * base_zoom), 1),
                         max(int(original.height * base_zoom), 1))
        if expected_size != fit_size:
            return
        self._neighbor_cache[path] = (original, fit_size, fit_image, dpr)

    def load_image(self):
        self.img_path = self.image_list[self.index]
        cached = self._neighbor_cache.pop(self.img_path, None)
        if cached is not None:
            original, fit_size, fit_image, cached_dpr = cached
            canvas_width = self.canvas.width()
            canvas_height = self.canvas.height()
            if (canvas_width > 1 and canvas_height > 1
                    and abs(cached_dpr - self._dpr) < 1e-6):
                base_zoom = min(canvas_width / original.width, canvas_height / original.height)
                expected_size = (max(int(original.width * base_zoom), 1),
                                 max(int(original.height * base_zoom), 1))
                if expected_size == fit_size:
                    self.original_image = original
                    self._cancel_pending_render()
                    self.zoom_level = 1.0
                    self._set_slider_zoom(1.0)
                    self._rendered_size = fit_size
                    self.view_offset_x = 0
                    self.view_offset_y = 0
                    self._set_display_image(fit_image, fit_size, cached_dpr)
                    self._refresh_displayed_image()
                    self._update_info_label()
                    self._prefetch_neighbors()
                    return
            self.original_image = original
        else:
            try:
                with Image.open(self.img_path) as img:
                    self.original_image = prepare_for_resampling(img)
            except Exception as e:
                logger.error("无法打开图片: {}\n{}", self.img_path, e)
                return

        self._cancel_pending_render()
        self.photo = None
        self._display_size = None
        self._rendered_size = None
        self.visible_size = None
        self.view_offset_x = 0
        self.view_offset_y = 0
        self.change_zoom(1.0, update_slider=True, reset_offsets=True)
        self._prefetch_neighbors()

    def open_external_and_copy(self):
        open_external_and_copy(self.img_path)

    def copy_current_file(self):
        copy_file_to_clipboard(self.img_path)

    def get_cached_image_info(self, img_path):
        info = None
        if self.info_cache is not None:
            info = self.info_cache.get(img_path)
        if info is None:
            info = get_image_info(img_path)
            if info is not None and self.info_cache is not None:
                self.info_cache[img_path] = info
        return info

    def get_cached_score(self, img_path, info=None):
        score = None
        if self.score_cache is not None:
            score = self.score_cache.get(img_path)
        if score is None:
            if info is None:
                info = self.get_cached_image_info(img_path)
            score = compute_score(img_path, self.screen_ratio, self.max_area, self.max_size, info)
            if self.score_cache is not None:
                self.score_cache[img_path] = score
        return score

    def closeEvent(self, event):
        if self._closed:
            super().closeEvent(event)
            return
        self._closed = True
        self._canvas_resize_timer.stop()
        self._render_timer.stop()
        self._cancel_neighbor_futures()
        self._neighbor_cache.clear()
        self._neighbor_executor.shutdown(wait=False, cancel_futures=True)
        self._preview_executor.shutdown(wait=False, cancel_futures=True)
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="图片浏览器 GUI")
    parser.add_argument("--folder", help="要扫描的文件夹路径")
    parser.add_argument("--page-size", type=int, default=12, help="每页显示的图片数量，默认为 12")
    parser.add_argument("--ratio", help="目标纵横比，例如 16:9")
    args = parser.parse_args()

    desired_ratio = None
    if args.ratio:
        try:
            a, b = args.ratio.split(":")
            desired_ratio = float(a) / float(b)
        except Exception:
            _console_print("比例格式应为 a:b")
            sys.exit(1)

    app = QApplication(sys.argv)
    apply_modern_style(app)
    screen = QGuiApplication.primaryScreen()
    geo = screen.geometry() if screen is not None else None
    if geo is not None:
        w = int(geo.width() * 0.75)
        h = int(geo.height() * 0.75)
        browser = ImageBrowser(
            folder=args.folder, page_size=args.page_size, desired_ratio=desired_ratio
        )
        browser.resize(w, h)
        browser.move(geo.center() - QPoint(w // 2, h // 2))
    else:
        browser = ImageBrowser(
            folder=args.folder, page_size=args.page_size, desired_ratio=desired_ratio
        )
    browser.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
