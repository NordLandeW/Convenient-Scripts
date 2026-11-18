"""Content-aware wallpaper scoring and bounded, cancellable folder analysis."""

import math
import os
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from operator import mul
from threading import Lock
from time import monotonic

from PIL import Image, ImageChops

ANALYSIS_EDGE = 256
ANALYSIS_CACHE_BYTES = 64 * 1024 * 1024
ANALYSIS_WORKERS = 4
PROGRESS_INTERVAL = 0.1  # seconds between ordinary GUI updates
COLOR_WEIGHT = 30.0
COLOR_SCALE = 45.0
INVALID_SCORE = -10000.0
_SQUARED_LEVELS = tuple(i * i for i in range(256))


@dataclass(frozen=True)
class Score:
    aspect: float
    resolution: float
    color: float
    format_penalty: float = 0.0

    @property
    def total(self):
        return self.aspect + self.resolution + self.color + self.format_penalty


@dataclass(frozen=True)
class Analysis:
    info: tuple
    sample: Image.Image


@dataclass
class ScanResult:
    images: list
    sorted_images: list
    info: dict
    scores: dict
    details: dict
    errors: dict


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


def visible_dimensions(width, height, target_ratio):
    if not math.isfinite(target_ratio) or target_ratio <= 0:
        raise ValueError("Target ratio must be finite and positive")
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")
    if width / height > target_ratio:
        return height * target_ratio, float(height)
    return float(width), width / target_ratio


def resolution_score(width, height, target_ratio, target_pixels):
    """Smoothly reward the pixels remaining after a centered cover crop."""
    if not math.isfinite(target_pixels) or target_pixels <= 0:
        raise ValueError("Target pixel count must be finite and positive")
    visible_width, visible_height = visible_dimensions(width, height, target_ratio)
    coverage = visible_width * visible_height / target_pixels
    return -40.0 - 85.0 * math.expm1(-2.0 * coverage)


def colorfulness(image):
    """Hasler-Suesstrunk M3, using signed opponent channels in 8-bit RGB."""
    count = image.width * image.height
    red, green, blue = image.split()
    r, g, b = (sum(map(mul, range(256), ch.histogram())) for ch in (red, green, blue))
    rg_mean = (r - g) / count
    yb_mean = (0.5 * (r + g) - b) / count

    def squared_difference(a, b):
        histogram = ImageChops.difference(a, b).histogram()
        return sum(map(mul, _SQUARED_LEVELS, histogram))

    # rg² + yb² = 3/4 (R-G)² + 1/2 ((R-B)² + (G-B)²). Absolute differences
    # fit Pillow's 8-bit histograms exactly; the signed means come from RGB above.
    sum_squares = 0.75 * squared_difference(red, green) + 0.5 * (
        squared_difference(red, blue) + squared_difference(green, blue)
    )
    variance = sum_squares / count - rg_mean**2 - yb_mean**2
    return math.sqrt(max(0.0, variance)) + 0.3 * math.hypot(rg_mean, yb_mean)


def score_analysis(analysis, target_ratio, target_pixels, extension=""):
    width, height, ratio, _file_size = analysis.info
    sample = analysis.sample
    visible_width, visible_height = visible_dimensions(sample.width, sample.height, target_ratio)
    crop_width = max(1, min(sample.width, round(visible_width)))
    crop_height = max(1, min(sample.height, round(visible_height)))
    left = (sample.width - crop_width) // 2
    top = (sample.height - crop_height) // 2
    visible = sample.crop((left, top, left + crop_width, top + crop_height))
    richness = colorfulness(visible)
    return Score(
        aspect=aspect_ratio_score(ratio, target_ratio),
        resolution=resolution_score(width, height, target_ratio, target_pixels),
        color=COLOR_WEIGHT * richness / (richness + COLOR_SCALE),
        format_penalty=-1000.0 if extension.lower() == ".gif" else 0.0,
    )


def read_analysis(path, file_size):
    with Image.open(path) as source:
        width, height = source.size
        info = (width, height, width / height, file_size)
        # Palette indices and hidden RGB must not become visible color. Convert
        # these modes before resampling; ordinary JPEGs may use decoder draft.
        if source.mode in ("RGBA", "LA", "PA") or "transparency" in source.info:
            sample = source.convert("RGBA")
        elif source.mode not in ("RGB", "L"):
            sample = source.convert("RGB")
        else:
            sample = source
        sample.thumbnail((ANALYSIS_EDGE, ANALYSIS_EDGE), Image.Resampling.BOX)
        if sample.mode == "RGBA":
            background = Image.new("RGBA", sample.size, (127, 127, 127, 255))
            sample = Image.alpha_composite(background, sample)
        sample = sample.convert("RGB")
    return Analysis(info, sample)


class AnalysisCache:
    """Thread-safe, byte-bounded LRU of immutable low-resolution source samples."""

    def __init__(self, max_bytes=ANALYSIS_CACHE_BYTES):
        self.max_bytes = max_bytes
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    @staticmethod
    def _stamp(path):
        stat = os.stat(path)
        return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def get(self, path):
        key = os.path.normcase(os.path.abspath(path))
        stamp = self._stamp(key)
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                old_stamp, analysis, size = cached
                if old_stamp == stamp:
                    self._entries.move_to_end(key)
                    return analysis
                self._entries.pop(key)
                self._bytes -= size
        analysis = read_analysis(key, stamp[0])
        if self._stamp(key) != stamp:
            raise OSError("Image changed during analysis; recalculate to retry")
        # Pillow's RGB core uses four bytes per pixel despite three channels.
        size = analysis.sample.width * analysis.sample.height * 4
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous[2]
            if size <= self.max_bytes:
                self._entries[key] = (stamp, analysis, size)
                self._bytes += size
            while self._bytes > self.max_bytes:
                _path, (_stamp, _analysis, removed_size) = self._entries.popitem(last=False)
                self._bytes -= removed_size
        return analysis


def collect_images(folder, cancel=None, found=None):
    if not os.path.isdir(folder):
        raise NotADirectoryError(folder)
    image_files = []
    allowed_ext = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff"}
    # Stream entries even in one large directory; keep os.walk's traversal order
    # because the final sort preserves discovery order for equal scores.
    folders = [folder]
    while folders:
        if cancel is not None and cancel.is_set():
            return []
        root = folders.pop()
        if root != folder and os.path.islink(root):
            continue
        subdirs = []
        with os.scandir(root) as entries:
            for entry in entries:
                if cancel is not None and cancel.is_set():
                    return []
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    is_dir = False  # Same classification as os.walk.
                if is_dir:
                    subdirs.append(entry.path)
                elif os.path.splitext(entry.name)[1].lower() in allowed_ext:
                    image_files.append(entry.path)
                    if found is not None:
                        found(len(image_files))
        folders.extend(reversed(subdirs))
    return image_files


def scan_folder(folder, target_ratio, target_pixels, cache, cancel, progress):
    """Run off the GUI thread; at most four decodes are outstanding at once."""
    next_update = 0.0

    def report(count, total):
        # Discovery has no denominator yet. Always publish phase boundaries and
        # the first completion; subsequent updates depend on time, not folder size.
        nonlocal next_update
        now = monotonic()
        if count <= 1 or count == total or now >= next_update:
            progress(count, total)
            next_update = now + PROGRESS_INTERVAL

    images = collect_images(folder, cancel, lambda found: report(found, None))
    if cancel.is_set():
        return None
    info, scores, details, errors = {}, {}, {}, {}
    paths = iter(images)
    total = len(images)
    completed = 0
    report(0, total)

    def analyze(path):
        if cancel.is_set():
            return None
        analysis = cache.get(path)
        if cancel.is_set():
            return None
        score = score_analysis(analysis, target_ratio, target_pixels, os.path.splitext(path)[1])
        return analysis.info, score

    with ThreadPoolExecutor(max_workers=ANALYSIS_WORKERS) as pool:
        pending = {}

        def submit_next():
            path = next(paths, None)
            if path is not None and not cancel.is_set():
                pending[pool.submit(analyze, path)] = path

        for _ in range(ANALYSIS_WORKERS):
            submit_next()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            if cancel.is_set():
                for future in pending:
                    future.cancel()
                return None
            for future in done:
                path = pending.pop(future)
                try:
                    result = future.result()
                    if result is not None:
                        image_info, score = result
                        info[path] = image_info
                        scores[path] = score.total
                        details[path] = score
                except Exception as error:
                    scores[path] = INVALID_SCORE
                    errors[path] = str(error)
                completed += 1
                report(completed, total)
                submit_next()
    if cancel.is_set():
        return None
    ordered = sorted(images, key=lambda path: scores[path], reverse=True)
    return ScanResult(images, ordered, info, scores, details, errors)
