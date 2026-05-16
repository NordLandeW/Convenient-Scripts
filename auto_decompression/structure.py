import os
import re

from housekeeping import _normalize_path_for_compare, print_info


def get_archive_base_name(filename):
    """Intelligently get the base name of a file, handling multi-volume archive extensions."""
    basename = os.path.basename(filename)
    
    # Regex for different archive parts, same as in group_archive_files
    part_regex = re.compile(r'(.+)\.part\d+\.rar$', re.IGNORECASE)
    r_regex = re.compile(r'(.+)\.r\d+$', re.IGNORECASE)
    num_regex = re.compile(r'(.+)\.(7z|zip)\.\d+$', re.IGNORECASE)
    z_regex = re.compile(r'(.+)\.z\d+$', re.IGNORECASE)
    
    match = part_regex.match(basename) or \
            r_regex.match(basename) or \
            num_regex.match(basename) or \
            z_regex.match(basename)
            
    if match:
        # If it's a known multi-volume format, return the captured base name
        return match.group(1)
    else:
        # Fallback for regular files (e.g., .zip, .rar, .7z)
        return os.path.splitext(basename)[0]


def list_related_archive_parts(file_path):
    """
    列出与指定压缩文件同属一个分卷集合的所有“原始压缩文件”路径（包含自身）。
    支持：
      - *.partNN.rar
      - *.rNN + *.rar
      - *.7z.001/002...
      - *.zip.001/002...
      - *.z01/z02... + *.zip
      - 单文件 *.zip/*.rar/*.7z
    """
    dir_name = os.path.dirname(file_path) or "."
    base_name = os.path.basename(file_path)
    files = os.listdir(dir_name)

    paths = set()

    # *.partNN.rar
    m = re.match(r'(.+)\.part\d+\.rar$', base_name, re.IGNORECASE)
    if m:
        base = m.group(1)
        pat = re.compile(rf'^{re.escape(base)}\.part\d+\.rar$', re.IGNORECASE)
        for f in files:
            if pat.match(f):
                paths.add(os.path.join(dir_name, f))
        return sorted(paths)

    # *.rNN (+ .rar)
    m = re.match(r'(.+)\.r\d+$', base_name, re.IGNORECASE)
    if m:
        base = m.group(1)
        rpat = re.compile(rf'^{re.escape(base)}\.r\d+$', re.IGNORECASE)
        for f in files:
            if rpat.match(f) or re.match(rf'^{re.escape(base)}\.rar$', f, re.IGNORECASE):
                paths.add(os.path.join(dir_name, f))
        if paths:
            return sorted(paths)

    # *.7z.001 or *.zip.001
    m = re.match(r'(.+)\.(7z|zip)\.\d+$', base_name, re.IGNORECASE)
    if m:
        base, ext = m.group(1), m.group(2)
        pat = re.compile(rf'^{re.escape(base)}\.{ext}\.\d+$', re.IGNORECASE)
        for f in files:
            if pat.match(f):
                paths.add(os.path.join(dir_name, f))
        if paths:
            return sorted(paths)

    # *.z01/z02... (+ .zip)
    m = re.match(r'(.+)\.z\d+$', base_name, re.IGNORECASE)
    if m:
        base = m.group(1)
        zpat = re.compile(rf'^{re.escape(base)}\.z\d+$', re.IGNORECASE)
        for f in files:
            if zpat.match(f) or re.match(rf'^{re.escape(base)}\.zip$', f, re.IGNORECASE):
                paths.add(os.path.join(dir_name, f))
        if paths:
            return sorted(paths)

    # *.zip (maybe with .zNN parts)
    m = re.match(r'(.+)\.zip$', base_name, re.IGNORECASE)
    if m:
        base = m.group(1)
        has_z = any(re.match(rf'^{re.escape(base)}\.z\d+$', f, re.IGNORECASE) for f in files)
        paths.add(os.path.join(dir_name, base_name))
        if has_z:
            zpat = re.compile(rf'^{re.escape(base)}\.z\d+$', re.IGNORECASE)
            for f in files:
                if zpat.match(f):
                    paths.add(os.path.join(dir_name, f))
        return sorted(paths)

    # *.rar (maybe with .rNN parts)
    m = re.match(r'(.+)\.rar$', base_name, re.IGNORECASE)
    if m:
        base = m.group(1)
        has_r = any(re.match(rf'^{re.escape(base)}\.r\d+$', f, re.IGNORECASE) for f in files)
        paths.add(os.path.join(dir_name, base_name))
        if has_r:
            rpat = re.compile(rf'^{re.escape(base)}\.r\d+$', re.IGNORECASE)
            for f in files:
                if rpat.match(f):
                    paths.add(os.path.join(dir_name, f))
        return sorted(paths)

    # *.7z (maybe with .7z.001 parts)
    m = re.match(r'(.+)\.7z$', base_name, re.IGNORECASE)
    if m:
        base = m.group(1)
        has_num = any(re.match(rf'^{re.escape(base)}\.7z\.\d+$', f, re.IGNORECASE) for f in files)
        if has_num:
            pat = re.compile(rf'^{re.escape(base)}\.7z\.\d+$', re.IGNORECASE)
            for f in files:
                if pat.match(f):
                    paths.add(os.path.join(dir_name, f))
            return sorted(paths)
        else:
            return [os.path.join(dir_name, base_name)]

    # 默认仅返回自身
    return [file_path]


def group_archive_files(directory):
    """
    Groups files in a directory into logical archives, handling multi-volume archives.
    Returns a list where each split archive contributes only its primary part.
    """
    files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]

    part_regex = re.compile(r'(.+)\.part(\d+)\.rar$', re.IGNORECASE)
    r_regex = re.compile(r'(.+)\.r(\d+)$', re.IGNORECASE)
    num_regex = re.compile(r'(.+)\.(7z|zip)\.(\d+)$', re.IGNORECASE)
    z_regex = re.compile(r'(.+)\.z(\d+)$', re.IGNORECASE)

    # Preserve on-disk naming while allowing case-insensitive lookup of companion files.
    lower_name_map = {}
    for f in files:
        lower_name_map.setdefault(f.casefold(), f)

    part_groups = {}
    r_groups = {}
    num_groups = {}
    z_groups = {}

    for f in files:
        m = part_regex.match(f)
        if m:
            base_name = m.group(1)
            index = int(m.group(2))
            part_groups.setdefault(base_name, []).append((index, f))
            continue

        m = r_regex.match(f)
        if m:
            base_name = m.group(1)
            index = int(m.group(2))
            r_groups.setdefault(base_name, []).append((index, f))
            continue

        m = num_regex.match(f)
        if m:
            base_name = m.group(1)
            ext = m.group(2).casefold()
            index = int(m.group(3))
            num_groups.setdefault((base_name, ext), []).append((index, f))
            continue

        m = z_regex.match(f)
        if m:
            base_name = m.group(1)
            index = int(m.group(2))
            z_groups.setdefault(base_name, []).append((index, f))

    primary_files = []
    processed_files = set()

    for _, parts in part_groups.items():
        first_part = min(parts, key=lambda item: (item[0], item[1].casefold()))[1]
        primary_files.append(first_part)
        processed_files.update(name for _, name in parts)

    for base_name, parts in r_groups.items():
        rar_file = lower_name_map.get(f"{base_name}.rar".casefold())
        if rar_file is not None:
            primary_files.append(rar_file)
            processed_files.add(rar_file)
        else:
            first_part = min(parts, key=lambda item: (item[0], item[1].casefold()))[1]
            primary_files.append(first_part)
        processed_files.update(name for _, name in parts)

    for _, parts in num_groups.items():
        first_part = min(parts, key=lambda item: (item[0], item[1].casefold()))[1]
        primary_files.append(first_part)
        processed_files.update(name for _, name in parts)

    for base_name, parts in z_groups.items():
        zip_file = lower_name_map.get(f"{base_name}.zip".casefold())
        if zip_file is not None:
            primary_files.append(zip_file)
            processed_files.add(zip_file)
        else:
            first_part = min(parts, key=lambda item: (item[0], item[1].casefold()))[1]
            primary_files.append(first_part)
        processed_files.update(name for _, name in parts)

    # Add all other non-volume files
    for f in files:
        if f not in processed_files:
            primary_files.append(f)
            
    return primary_files


def is_split_volume_member(filename: str) -> bool:
    """Returns True when the filename is one member of a split archive set."""
    return bool(
        re.match(r'.+\.part\d+\.rar$', filename, re.IGNORECASE)
        or re.match(r'.+\.r\d+$', filename, re.IGNORECASE)
        or re.match(r'.+\.(7z|zip)\.\d+$', filename, re.IGNORECASE)
        or re.match(r'.+\.z\d+$', filename, re.IGNORECASE)
    )


def filter_non_primary_split_inputs(file_paths):
    """Keeps input order and redirects non-primary split members to their primary part."""
    if not file_paths:
        return []

    primary_names_by_dir = {}
    for path in file_paths:
        dir_name = os.path.dirname(path) or "."
        if dir_name in primary_names_by_dir:
            continue
        try:
            primary_names_by_dir[dir_name] = {
                name.casefold() for name in group_archive_files(dir_name)
            }
        except Exception:
            primary_names_by_dir[dir_name] = set()

    filtered_paths = []
    seen_input_paths = set()
    seen_output_paths = set()

    for path in file_paths:
        normalized_input_path = _normalize_path_for_compare(path)
        if normalized_input_path in seen_input_paths:
            continue
        seen_input_paths.add(normalized_input_path)

        candidate_path = path
        redirect_message = None
        file_name = os.path.basename(path)

        if is_split_volume_member(file_name):
            dir_name = os.path.dirname(path) or "."
            primary_names = primary_names_by_dir.get(dir_name, set())

            if file_name.casefold() not in primary_names:
                redirected_path = None
                try:
                    related_paths = list_related_archive_parts(path)
                except Exception:
                    related_paths = []

                for related_path in related_paths:
                    related_name = os.path.basename(related_path)
                    if related_name.casefold() in primary_names:
                        redirected_path = related_path
                        break

                if redirected_path is None:
                    print_info(f"跳过非首卷分卷文件：{path}")
                    continue

                candidate_path = redirected_path
                redirect_message = f"将非首卷分卷文件重定向到首卷：{path} -> {candidate_path}"

        normalized_output_path = _normalize_path_for_compare(candidate_path)
        if normalized_output_path in seen_output_paths:
            continue
        seen_output_paths.add(normalized_output_path)

        if redirect_message:
            print_info(redirect_message)

        filtered_paths.append(candidate_path)

    return filtered_paths
def is_likely_archive_filename(name: str) -> bool:
    """Heuristic check to determine if a file is likely an archive based on its extension."""
    lower = name.lower()

    patterns = [
        r".+\.part\d+\.rar$",       # xxx.part01.rar 等
        r".+\.r\d+$",               # xxx.r00 等（配合 .rar）
        r".+\.(7z|zip)\.\d+$",      # xxx.7z.001 / xxx.zip.001
        r".+\.z\d+$",               # xxx.z01 / xxx.z02（配合 .zip）
        r".+\.zip$",                # 单文件 zip
        r".+\.rar$",                # 单文件 rar
        r".+\.7z$",                 # 单文件 7z
        r".+\.tar(\.\w+)?$",        # .tar / .tar.gz / .tar.bz2 / .tar.xz 等
        r".+\.iso$",                # 常见镜像格式，7z 也可以解
    ]

    for pat in patterns:
        if re.match(pat, lower, re.IGNORECASE):
            return True
    return False
