"""Publish a terminal layer while preserving its naming and flattening rules."""

import os
import shutil

import send2trash

from console import print_success
from housekeeping import (
    _is_reserved_path,
    _normalize_path_for_compare,
    _pick_unique_name,
    create_unique_directory,
    detect_single_same_named_file,
    move_file_with_unique_suffix,
    move_path_with_collision_handling,
    should_flatten_prefixed_files,
)


def publish_output(
    temp_folder,
    base_folder,
    last_compressed_file_name,
    *,
    auto_flatten_single_file,
    extract_to_base_folder,
    source_archive_paths,
    trash_on_success,
    recycled_reserved_paths,
):
    allow_replace_reserved = bool(source_archive_paths and trash_on_success)
    flattened_output_path = None
    try:
        temp_entries = os.listdir(temp_folder)
    except FileNotFoundError:
        temp_entries = []

    if auto_flatten_single_file and not extract_to_base_folder and temp_entries:
        single_file_path = detect_single_same_named_file(
            temp_folder, last_compressed_file_name, entries=temp_entries
        )
        if single_file_path:
            flattened_output_path = move_file_with_unique_suffix(
                single_file_path,
                base_folder,
                reserved_paths=source_archive_paths,
                allow_replace_reserved=allow_replace_reserved,
                recycled_reserved_paths=recycled_reserved_paths,
            )
            print_success(
                f"检测到 {last_compressed_file_name}/"
                f"{os.path.basename(flattened_output_path)} 结构喵，"
                f"已直接将文件放置到目标目录：{flattened_output_path}"
            )

    if not flattened_output_path:
        # Prefix flattening is independent of the single-file option. Keep this
        # order and predicate: turning off one rule does not turn off the other.
        flatten_due_to_prefix = (
            (not extract_to_base_folder)
            and should_flatten_prefixed_files(
                temp_folder, temp_entries, last_compressed_file_name
            )
        )
        if extract_to_base_folder or flatten_due_to_prefix:
            target_folder = base_folder
            for entry in temp_entries:
                move_path_with_collision_handling(
                    os.path.join(temp_folder, entry),
                    target_folder,
                    reserved_paths=source_archive_paths,
                    allow_replace_reserved=allow_replace_reserved,
                    recycled_reserved_paths=recycled_reserved_paths,
                )
            if flatten_due_to_prefix and not extract_to_base_folder:
                print_success(
                    f"检测到 {last_compressed_file_name}/XY 前缀结构喵，"
                    f"已移除 {last_compressed_file_name}/ 层级，"
                    f"最终文件被移动到：{target_folder}"
                )
            else:
                print_success(f"最终文件被移动到：{target_folder}")
        else:
            desired_target_folder = os.path.join(base_folder, last_compressed_file_name)
            needs_reserved_replacement = (
                allow_replace_reserved
                and os.path.exists(desired_target_folder)
                and _is_reserved_path(desired_target_folder, source_archive_paths)
            )
            if needs_reserved_replacement:
                target_folder = create_unique_directory(
                    base_folder, f"{last_compressed_file_name}.AutoDecTmp"
                )
            else:
                target_folder = create_unique_directory(base_folder, last_compressed_file_name)
            for entry in temp_entries:
                shutil.move(os.path.join(temp_folder, entry), target_folder)

            if needs_reserved_replacement:
                final_target_folder = target_folder
                try:
                    send2trash.send2trash(desired_target_folder)
                    os.rename(target_folder, desired_target_folder)
                    final_target_folder = desired_target_folder
                    recycled_reserved_paths.add(
                        _normalize_path_for_compare(desired_target_folder)
                    )
                except Exception:
                    fallback_name = _pick_unique_name(
                        base_folder, last_compressed_file_name, is_dir=True
                    )
                    fallback_path = os.path.join(base_folder, fallback_name)
                    try:
                        os.rename(target_folder, fallback_path)
                        final_target_folder = fallback_path
                    except Exception:
                        final_target_folder = target_folder
                target_folder = final_target_folder
            print_success(f"最终文件被移动到：{target_folder}")
