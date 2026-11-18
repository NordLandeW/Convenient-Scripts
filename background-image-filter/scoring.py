"""Content-aware wallpaper scoring and bounded, cancellable folder analysis."""

import math
import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from operator import mul
from time import monotonic

from PIL import Image, ImageChops

ANALYSIS_EDGE = 128
_logical_cpu_count = getattr(os, "process_cpu_count", os.cpu_count)
PROGRESS_INTERVAL = 0.1  # seconds between ordinary GUI updates
COLOR_WEIGHT = 40.0
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
    color: float


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
    return Score(
        aspect=aspect_ratio_score(ratio, target_ratio),
        resolution=resolution_score(width, height, target_ratio, target_pixels),
        color=analysis.color,
        format_penalty=-1000.0 if extension.lower() == ".gif" else 0.0,
    )


def read_analysis(path, file_size):
    with Image.open(path) as source:
        width, height = source.size
        info = (width, height, width / height, file_size)
        # Palette indices and hidden RGB must not become visible color. Convert
        # these modes before resampling; ordinary JPEGs may use decoder draft.
        if source.mode == "RGBA":
            sample = source
        elif source.mode in ("LA", "PA") or "transparency" in source.info:
            sample = source.convert("RGBA")
        elif source.mode not in ("RGB", "L"):
            sample = source.convert("RGB")
        else:
            sample = source
        sample.thumbnail((ANALYSIS_EDGE, ANALYSIS_EDGE), Image.Resampling.BOX)
        if sample.mode == "RGBA":
            background = Image.new("RGBA", sample.size, (127, 127, 127, 255))
            sample = Image.alpha_composite(background, sample)
        richness = colorfulness(sample if sample.mode == "RGB" else sample.convert("RGB"))
    return Analysis(info, COLOR_WEIGHT * richness / (richness + COLOR_SCALE))


def _image_entries(folder, cancel):
    if not os.path.isdir(folder):
        raise NotADirectoryError(folder)
    folder = os.path.abspath(folder)
    allowed_ext = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff"}
    # Stream entries even in one large directory; keep os.walk's traversal order
    # because the final sort preserves discovery order for equal scores.
    folders = [folder]
    while folders:
        if cancel.is_set():
            return
        root = folders.pop()
        if root != folder and os.path.islink(root):
            continue
        subdirs = []
        with os.scandir(root) as entries:
            for entry in entries:
                if cancel.is_set():
                    return
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    is_dir = False  # Same classification as os.walk.
                if is_dir:
                    subdirs.append(entry.path)
                elif os.path.splitext(entry.name)[1].lower() in allowed_ext:
                    yield entry
        folders.extend(reversed(subdirs))


def _stamp(stat):
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _decode(path, stamp):
    analysis = read_analysis(path, stamp[0])
    if _stamp(os.stat(path)) != stamp:
        raise OSError("Image changed during analysis; recalculate to retry")
    return analysis


def scan_folder(folder, target_ratio, target_pixels, cache, cancel, progress):
    """Scan off the GUI thread with a bounded number of concurrent decodes."""
    next_update = 0.0

    def report(count, total, *, immediate=False):
        # Discovery has no denominator yet. Always publish phase boundaries and
        # the first completion; subsequent updates depend on time, not folder size.
        nonlocal next_update
        now = monotonic()
        if immediate or count <= 1 or count == total or now >= next_update:
            progress(count, total)
            next_update = now + PROGRESS_INTERVAL

    # Only the scan coordinator touches this cache. The GUI serializes scans;
    # decoder threads return immutable geometry and color, never image buffers.
    images, misses = [], []
    info, scores, details, errors = {}, {}, {}, {}

    def record(path, analysis):
        score = score_analysis(analysis, target_ratio, target_pixels, os.path.splitext(path)[1])
        info[path] = analysis.info
        scores[path] = score.total
        details[path] = score

    for entry in _image_entries(folder, cancel):
        path = entry.path
        images.append(path)
        report(len(images), None)
        try:
            stamp = _stamp(entry.stat())
        except OSError as error:
            scores[path] = INVALID_SCORE
            errors[path] = str(error)
            cache.pop(path, None)
            continue
        cached = cache.get(path)
        if cached is not None and cached[0] == stamp:
            record(path, cached[1])
        else:
            misses.append((path, stamp))
    if cancel.is_set():
        return None
    for stale in cache.keys() - images:
        del cache[stale]
    paths = iter(misses)
    total = len(images)
    completed = total - len(misses)
    report(completed, total, immediate=True)

    workers = max(1, (_logical_cpu_count() or 1) * 4 // 5)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {}

        def submit_next():
            item = next(paths, None)
            if item is not None and not cancel.is_set():
                pending[pool.submit(_decode, *item)] = item

        for _ in range(min(workers, len(misses))):
            submit_next()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            if cancel.is_set():
                for future in pending:
                    future.cancel()
                return None
            for future in done:
                path, stamp = pending.pop(future)
                try:
                    analysis = future.result()
                    record(path, analysis)
                    cache[path] = (stamp, analysis)
                except Exception as error:
                    cache.pop(path, None)
                    scores[path] = INVALID_SCORE
                    errors[path] = str(error)
                completed += 1
                report(completed, total)
                submit_next()
    if cancel.is_set():
        return None
    ordered = sorted(images, key=lambda path: scores[path], reverse=True)
    return ScanResult(images, ordered, info, scores, details, errors)
