import os
import shutil
import sys

import send2trash
from rich.console import Console

console = Console()
RECOVER_SUFFIX = ".AutoDecRecovered"

# When extracted output reuses the source archive filename, we must avoid trashing the output
# later in the global "trash-on-success" step.
_RECYCLED_RESERVED_PATHS = set()


def print_info(message):
    console.out(message, style="blue")


def print_error(message):
    console.out(message, style="bold red")


def print_success(message):
    console.out(message, style="green")


def print_warning(message):
    console.out(message, style="bold yellow underline")


def _ensure_directory(path, label):
    if os.path.exists(path):
        return
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        print_error(f"无法创建{label}目录 {path} 喵：{e}")
        sys.exit(1)


def move_temp_folders_to_recycle_bin(current_directory):
    """Cleans up leftover temporary extraction folders."""
    items = os.listdir(current_directory)

    temp_folders = [
        item
        for item in items
        if os.path.isdir(os.path.join(current_directory, item))
        and item.startswith("temp_extract")
    ]
    if len(temp_folders) == 0:
        return False

    # 将所有符合条件的子文件夹移动到系统回收站中
    recycled = False
    for folder in temp_folders:
        folder_path = os.path.join(current_directory, folder)
        try:
            send2trash.send2trash(folder_path)
            print_info(f"将 {folder_path} 移动到了回收站喵☆")
            recycled = True
        except Exception as e:
            pass
    return recycled


def remove_autodec_files(directory):
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(RECOVER_SUFFIX):
                file_path = os.path.join(root, file)
                try:
                    os.remove(file_path)
                    print_info(f"移除了临时文件 {file_path} 喵！")
                except Exception as e:
                    print_error(f"移除临时文件 {file_path} 时出现错误喵: {e}")


def create_unique_directory(base_path, dir_name):
    """Creates a directory with a unique name to avoid collisions."""
    counter = 1
    original_dir_name = dir_name
    while os.path.exists(os.path.join(base_path, dir_name)):
        dir_name = f"{original_dir_name}~{counter}"
        counter += 1
    os.makedirs(os.path.join(base_path, dir_name))
    print_success(f"创建目录：{dir_name}")
    return os.path.join(base_path, dir_name)


def _normalize_path_for_compare(path: str) -> str:
    """Normalizes paths for reliable comparisons across platforms/case rules."""
    return os.path.normcase(os.path.abspath(path))


def _is_reserved_path(path: str, reserved_paths: set) -> bool:
    if not reserved_paths:
        return False
    return _normalize_path_for_compare(path) in reserved_paths


def _pick_unique_name(dest_dir: str, desired_name: str, is_dir: bool) -> str:
    """Generates a non-colliding name by appending '~N' while preserving file extensions."""
    candidate = desired_name
    stem, ext = os.path.splitext(desired_name)
    counter = 1
    while os.path.exists(os.path.join(dest_dir, candidate)):
        candidate = f"{desired_name}~{counter}" if is_dir else f"{stem}~{counter}{ext}"
        counter += 1
    return candidate


def _pick_temp_name(dest_dir: str, desired_name: str, is_dir: bool) -> str:
    """Produces a transient name used to stage moves before replacing a reserved destination."""
    if is_dir:
        base = f"{desired_name}.AutoDecTmp"
        return _pick_unique_name(dest_dir, base, is_dir=True)

    stem, ext = os.path.splitext(desired_name)
    base = f"{stem}.AutoDecTmp{ext}"
    return _pick_unique_name(dest_dir, base, is_dir=False)


def move_path_with_collision_handling(
    src_path: str,
    dest_dir: str,
    reserved_paths: set = None,
    allow_replace_reserved: bool = False,
) -> str:
    """
    Moves a file or directory into dest_dir while keeping names stable when possible.

    When a collision is caused only by a 'reserved' path (typically the source archive
    that will be moved to recycle bin on success), the function stages the move under a
    temporary name first, then replaces the reserved path and renames into place. This
    avoids producing unnecessary '~1' suffixes.
    """
    desired_name = os.path.basename(src_path)
    is_dir = os.path.isdir(src_path)
    desired_path = os.path.join(dest_dir, desired_name)

    if not os.path.exists(desired_path):
        shutil.move(src_path, desired_path)
        return desired_path

    if allow_replace_reserved and _is_reserved_path(desired_path, reserved_paths):
        temp_name = _pick_temp_name(dest_dir, desired_name, is_dir=is_dir)
        temp_path = os.path.join(dest_dir, temp_name)

        # Stage first so we don't lose output if recycling the source archive fails.
        shutil.move(src_path, temp_path)

        try:
            send2trash.send2trash(desired_path)
        except Exception:
            fallback_name = _pick_unique_name(dest_dir, desired_name, is_dir=is_dir)
            fallback_path = os.path.join(dest_dir, fallback_name)
            try:
                os.rename(temp_path, fallback_path)
            except Exception:
                shutil.move(temp_path, fallback_path)
            return fallback_path

        try:
            os.rename(temp_path, desired_path)
            _RECYCLED_RESERVED_PATHS.add(_normalize_path_for_compare(desired_path))
            return desired_path
        except Exception:
            fallback_name = _pick_unique_name(dest_dir, desired_name, is_dir=is_dir)
            fallback_path = os.path.join(dest_dir, fallback_name)
            shutil.move(temp_path, fallback_path)
            return fallback_path

    fallback_name = _pick_unique_name(dest_dir, desired_name, is_dir=is_dir)
    fallback_path = os.path.join(dest_dir, fallback_name)
    shutil.move(src_path, fallback_path)
    return fallback_path


def move_file_with_unique_suffix(
    src_path: str,
    dest_dir: str,
    reserved_paths: set = None,
    allow_replace_reserved: bool = False,
) -> str:
    """Backward-compatible wrapper around `move_path_with_collision_handling()` for files."""
    return move_path_with_collision_handling(
        src_path,
        dest_dir,
        reserved_paths=reserved_paths,
        allow_replace_reserved=allow_replace_reserved,
    )


def should_flatten_prefixed_files(temp_folder: str, entries: list, folder_name: str) -> bool:
    """
    Detects 'X/XY' layouts where the extra 'X' directory provides no value.

    If every extracted entry is a file whose name starts with the would-be output folder
    name, flattening avoids an extra nesting level without losing disambiguation.
    """
    if not entries:
        return False
    if not folder_name:
        return False

    prefix = folder_name.casefold()
    for entry in entries:
        entry_path = os.path.join(temp_folder, entry)
        if not os.path.isfile(entry_path):
            return False
        if not entry.casefold().startswith(prefix):
            return False
    return True


def detect_single_same_named_file(temp_folder, expected_base_name, entries=None):
    """Checks if the folder contains exactly one file matching the archive's base name."""
    try:
        items = entries if entries is not None else os.listdir(temp_folder)
    except FileNotFoundError:
        return None
    if len(items) != 1:
        return None
    entry = items[0]
    entry_path = os.path.join(temp_folder, entry)
    if not os.path.isfile(entry_path):
        return None
    entry_base, _ = os.path.splitext(entry)
    if entry_base.lower() != expected_base_name.lower():
        return None
    return entry_path
