#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unified CLI for "rename" (flatten with underscores) and "repack" (restore paths)
Refactored from the original rename.py and repack.py without changing core logic.

- Subcommands:
  * rename: replicate original rename.py behavior (using .rename_lib)
  * repack: replicate original repack.py behavior (classic or using .rename_lib)

Notes on behavior preservation:
- The core renaming/restoring algorithms are kept intact, including the quirky path handling.
- For "rename", the head offset logic defaults to 2 when root is ".", matching original behavior.
- For "repack", if .rename_lib is missing, fallback to classic underscore-based restore.
- We removed interactive pauses by default; optional flags are provided to preserve cleanup behaviors.
"""

import argparse
import json
import os
import sys
from typing import Dict, Optional

from loguru import logger

# Folders containing any of these keywords (case-insensitive) will be skipped during 'rename'.
EXCLUDE_KEYWORDS = ["old"]


def setup_logging():
    logger.remove()
    logger.add(sys.stdout, level="INFO", format="{message}")


def _rename_walk(
    dir_path: str,
    head: int,
    floor: int,
    library: Dict[str, str],
    collapse_self_dir: bool,
) -> None:
    entries = os.scandir(dir_path)
    for entry in entries:
        if entry.is_file():
            relative_path = entry.path[head:]
            new_name = entry.path[:head] + relative_path.replace(os.sep, "_")
            if os.altsep:
                new_name = new_name.replace(os.altsep, "_")
            if collapse_self_dir:
                normalized_rel = relative_path.replace(os.sep, "/")
                if os.altsep:
                    normalized_rel = normalized_rel.replace(os.altsep, "/")
                if normalized_rel.count("/") == 1:
                    parent_candidate, file_part = normalized_rel.split("/", 1)
                    file_stem, file_ext = os.path.splitext(file_part)
                    if file_stem == parent_candidate:
                        new_name = entry.path[:head] + file_stem + file_ext
            if new_name != entry.path:
                if not os.path.exists(new_name):
                    if entry.path in library:
                        logger.info("File %s has been renamed. Skipped." % (entry.path))
                    else:
                        logger.info("Rename %s to %s." % (entry.path, new_name))
                        os.rename(entry.path, new_name)
                        library[new_name] = entry.path
                else:
                    logger.info("File %s has existed. Skipped." % new_name)
        else:
            # Skip folders containing any of the excluded keywords (case-insensitive)
            name_lower = entry.name.lower()
            if any(kw.lower() in name_lower for kw in EXCLUDE_KEYWORDS):
                logger.info("Skip excluded folder: %s" % entry.path)
                continue

            # Preserve original recursion and path concatenation
            subdir = os.path.join(dir_path, entry.name)
            _rename_walk(
                subdir,
                head + (len(entry.name) + 1 if floor > 0 else 0),
                floor - 1,
                library,
                collapse_self_dir,
            )
            if floor <= 0:
                try:
                    os.rmdir(entry.path)
                except Exception:
                    pass

def cmd_rename(root: str, floor: int, lib_path: str, collapse_self_dir: bool) -> None:
    root_abs = os.path.abspath(root)
    lib_abs = os.path.abspath(lib_path)

    try:
        with open(lib_abs, "r", encoding="utf-8") as f:
            library = json.load(f)
        logger.info("Found rename library.")
    except Exception:
        logger.info("Create a new rename library.")
        library = dict()

    old_cwd = os.getcwd()
    try:
        os.chdir(root_abs)
        head = 2
        _rename_walk(".", head, floor, library, collapse_self_dir)
    finally:
        os.chdir(old_cwd)

    with open(lib_abs, "w", encoding="utf-8") as f:
        json.dump(library, f, ensure_ascii=False)


def cmd_repack(root: str, lib_path: str, keep_lib: bool) -> None:
    root_abs = os.path.abspath(root)
    lib_abs = os.path.abspath(lib_path)

    def _normalize_relative(path_str: str) -> Optional[str]:
        if path_str is None:
            return None
        path_std = path_str.replace("/", os.sep)
        path_std = path_std.replace("\\", os.sep)
        if os.path.isabs(path_std):
            abs_candidate = os.path.normpath(path_std)
        else:
            abs_candidate = os.path.normpath(os.path.join(root_abs, path_std))
        try:
            common = os.path.commonpath([root_abs, abs_candidate])
        except ValueError:
            return None
        if common != root_abs:
            return None
        rel_path = os.path.relpath(abs_candidate, root_abs)
        if rel_path == ".":
            return ""
        return rel_path

    def _rel_to_abs(rel_path: str) -> Optional[str]:
        if rel_path is None:
            return None
        rel_std = rel_path.replace("/", os.sep)
        rel_std = rel_std.replace("\\", os.sep)
        abs_candidate = os.path.normpath(os.path.join(root_abs, rel_std))
        try:
            common = os.path.commonpath([root_abs, abs_candidate])
        except ValueError:
            return None
        if common != root_abs:
            return None
        return abs_candidate

    library: Optional[Dict[str, str]] = None
    try:
        with open(lib_abs, "r", encoding="utf-8") as f:
            raw_library = json.load(f)
        normalized_library: Dict[str, str] = {}
        for flattened, original in raw_library.items():
            key_rel = _normalize_relative(flattened)
            value_rel = _normalize_relative(original)
            if key_rel is None or value_rel is None:
                logger.info(f"Library entry {flattened} -> {original} is outside root scope. Skip.")
                continue
            normalized_library[key_rel] = value_rel
        library = normalized_library
        logger.info("Found rename library.")
    except Exception:
        library = None
        logger.info("Library not found. Fallback to classic methods.")

    with os.scandir(root_abs) as entries:
        for entry in entries:
            if not entry.is_file():
                continue

            entry_rel = _normalize_relative(entry.path)
            if entry_rel is None or entry_rel == "":
                logger.info(f"Unable to normalize path for {entry.path}. Skip.")
                continue

            target_rel: Optional[str]
            if library is None:
                flattened_name = entry_rel
                if "_" not in flattened_name and "-U" not in flattened_name:
                    continue
                name_chars = list(flattened_name)
                underscore_idx = flattened_name.rfind("_")
                if underscore_idx != -1:
                    name_chars[underscore_idx] = os.sep
                candidate = "".join(name_chars)
                candidate = candidate.replace("_", os.sep).replace("-U", "_")
                target_rel = _normalize_relative(candidate)
            else:
                target_rel = library.get(entry_rel)
                if target_rel is None:
                    logger.info(f"{entry.path} not in library file. Skip.")
                    continue

            if not target_rel:
                logger.info(f"Target path for {entry.path} is empty. Skip.")
                continue

            target_abs = _rel_to_abs(target_rel)
            if target_abs is None:
                logger.info(f"Target path {target_rel} escapes root {root_abs}. Skip.")
                continue

            logger.info("Rename %s to %s" % (entry.path, target_abs))

            target_dir = os.path.dirname(target_abs)
            if target_dir and not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)

            if not os.path.exists(target_abs):
                os.rename(entry.path, target_abs)
            else:
                logger.info("File %s has existed. Skip." % target_abs)

    if not keep_lib:
        try:
            os.remove(lib_abs)
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Image unfolder script: rename (flatten) and repack (restore) utilities."
    )
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser(
        "rename",
        help="Flatten file paths into underscores; records mapping in .rename_lib.",
    )
    pr.add_argument(
        "--dir",
        default=".",
        help="Root directory to process. Default: current directory (.)",
    )
    pr.add_argument(
        "--floor",
        type=int,
        default=0,
        help="Number of top levels to keep before flattening. Default: 0",
    )
    pr.add_argument(
        "--lib",
        default=None,
        help="Path to rename library file. Default: <dir>/.rename_lib",
    )
    pr.add_argument(
        "--no-collapse-self-dir",
        dest="collapse_self_dir",
        action="store_false",
        default=True,
        help="Disable collapsing <name>/<name>.<ext> into <name>.<ext>; default behavior keeps it collapsed.",
    )

    pp = sub.add_parser(
        "repack",
        help="Restore file paths using .rename_lib or classic underscore-based method.",
    )
    pp.add_argument(
        "--dir",
        default=".",
        help="Root directory to process. Default: current directory (.)",
    )
    pp.add_argument(
        "--lib",
        default=None,
        help="Path to rename library file. Default: <dir>/.rename_lib",
    )
    pp.add_argument(
        "--keep-lib",
        action="store_true",
        help="Keep the rename library after repack (original repack.py removed it). Default: remove.",
    )

    return p


def main(argv=None):
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "rename":
        lib_path = args.lib if args.lib else os.path.join(args.dir, ".rename_lib")
        cmd_rename(
            root=args.dir,
            floor=args.floor,
            lib_path=lib_path,
            collapse_self_dir=args.collapse_self_dir,
        )
    elif args.command == "repack":
        lib_path = args.lib if args.lib else os.path.join(args.dir, ".rename_lib")
        cmd_repack(root=args.dir, lib_path=lib_path, keep_lib=args.keep_lib)
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
