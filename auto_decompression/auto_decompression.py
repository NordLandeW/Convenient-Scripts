import json
import subprocess
import os
import sys
import shutil
import traceback
import argparse
import extract_hidden_zip as hiddenZip
import send2trash
from rich.console import Console
from rich.progress import Progress
import rich.progress
import requests
from platformdirs import PlatformDirs
import datetime as _dt
import atexit
import time

__version__ = "1.2.1"
console = Console()
_dirs = PlatformDirs(appname="auto_decompression", appauthor="NordLandeW")
CONFIG_DIR = _dirs.user_config_dir
DATA_DIR = _dirs.user_data_dir
extract_to_base_folder = False
auto_flatten_single_file = True
pwdFilename = "dict.json"
pwdDictionary = {}
RECOVER_SUFFIX = ".AutoDecRecovered"
DEFAULT_EMBEDDED_SCAN_MAX_LEVEL = 2  # Max recursion depth to check for hidden embedded files
embedded_scan_depth_setting = DEFAULT_EMBEDDED_SCAN_MAX_LEVEL
CLI_ARGS = None
SMALL_NON_ARCHIVE_IGNORE_THRESHOLD = 20 * 1024  # Threshold in bytes to ignore small non-archive files during recursion

# When extracted output reuses the source archive filename, we must avoid trashing the output
# later in the global "trash-on-success" step.
_RECYCLED_RESERVED_PATHS = set()

GIST_CONFIG_FILE = "gist_config.json"
_gist_cfg = None  # {token:str, gist_id:str, file:str}
_gist_remote_ts = None  # 上一次拉取时远程文件 updated_at（datetime）


def _cfg_path(fname):
    return os.path.join(CONFIG_DIR, fname)

def _load_gist_config():
    cfg_path = _cfg_path(GIST_CONFIG_FILE)
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def _save_gist_config(cfg):
    cfg_path = _cfg_path(GIST_CONFIG_FILE)
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print_warning(f"保存 Gist 配置失败喵：{e}")

def _gist_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

def _fetch_from_gist(cfg):
    """
    Fetches password dictionary from GitHub Gist.
    
    Returns:
        tuple: (content_dict, updated_at_str) or (None, None) on failure.
    """
    try:
        r = requests.get(
            f"https://api.github.com/gists/{cfg['gist_id']}",
            headers=_gist_headers(cfg['token']),
        )
        if r.status_code == 200:
            gist = r.json()
            file_info = gist["files"].get(cfg["file"])
            if file_info and file_info.get("content") is not None:
                # Use gist-level updated_at as individual files don't have it
                return json.loads(file_info["content"] or "{}"), gist["updated_at"]
    except Exception as e:
        print_warning(f"拉取 Gist 时出错喵：{e}")
    return None, None

def _update_gist(cfg, content_str):
    try:
        payload = {"files": {cfg["file"]: {"content": content_str}}}
        r = requests.patch(
            f"https://api.github.com/gists/{cfg['gist_id']}",
            headers=_gist_headers(cfg['token']),
            json=payload,
        )
        return r.status_code == 200
    except Exception as e:
        print_warning(f"更新 Gist 时出错喵：{e}")
        return False

def _create_new_gist(token, file_name):
    payload = {
        "description": "password‑dict sync",
        "public": False,
        "files": {file_name: {"content": "{}"}},
    }
    r = requests.post("https://api.github.com/gists", headers=_gist_headers(token), json=payload)
    if r.status_code == 201:
        return r.json()["id"]
    print_error(f"创建 Gist 失败喵：{r.text}")
    sys.exit(1)

def _setup_gist_interactive():
    global pwdDictionary
    global _gist_remote_ts
    console.print("[cyan][b]检测到未配置 Gist，同步向导启动喵~")
    console.print("[cyan][b]请输入 GitHub Token（需 gist 权限）喵：", end="")
    token = input().strip()
    console.print("[cyan][b]请输入已有 Gist ID 或直接回车自动创建喵：", end="")
    gist_id = input().strip()
    file_name = pwdFilename
    
    # 创建配置对象
    cfg = {"token": token, "gist_id": gist_id, "file": file_name}
    
    # 检查是否提供了已有的 Gist ID
    if gist_id != "":
        # 检查本地是否有密码本
        local_pwd_path = os.path.join(DATA_DIR, pwdFilename)
        has_local_pwd = os.path.exists(local_pwd_path)
        
        # 尝试获取远程密码本信息
        remote_dict, remote_ts_str = _fetch_from_gist(cfg)
        
        if remote_dict is not None:
            # 远程 Gist 存在且可访问
            if has_local_pwd:
                # 本地和远程都存在，询问用户选择
                local_mtime = _dt.datetime.fromtimestamp(os.path.getmtime(local_pwd_path), tz=_dt.timezone.utc)
                remote_ts = _dt.datetime.fromisoformat(remote_ts_str.replace("Z", "+00:00")) if remote_ts_str else None
                
                print_info(f"检测到本地密码本（最后修改时间：{local_mtime.astimezone().strftime('%Y-%m-%d %H:%M:%S')}）")
                print_info(f"远程密码本（最后修改时间：{remote_ts.astimezone().strftime('%Y-%m-%d %H:%M:%S') if remote_ts else '未知'}）")
                
                console.print("[cyan][b]请选择操作：[1] 拉取远程密码本 [2] 上传本地密码本 [默认:1]：", end="")
                choice = input().strip()
                
                if choice == "2":
                    # 用户选择上传本地密码本
                    print_info("将使用本地密码本并上传到 Gist")
                    # 配置已创建，保存后会在退出时自动上传
                else:
                    # 用户选择拉取远程密码本或默认选项
                    pwdDictionary = remote_dict
                    _gist_remote_ts = remote_ts
                    save_passwords()
                    print_success("已从 Gist 拉取密码本喵！")
            else:
                # 本地不存在但远程存在，直接拉取
                pwdDictionary = remote_dict
                _gist_remote_ts = _dt.datetime.fromisoformat(remote_ts_str.replace("Z", "+00:00")) if remote_ts_str else None
                save_passwords()
                print_success("已从 Gist 拉取密码本喵！")
        else:
            # 远程 Gist 不存在或无法访问
            print_warning(f"无法访问指定的 Gist ID：{gist_id}，请检查 ID 是否正确或网络连接是否正常")
            console.print("[cyan][b]是否要创建新的 Gist？[Y/n]：", end="")
            create_new = input().strip().lower()
            if create_new != "n":
                gist_id = _create_new_gist(token, file_name)
                cfg["gist_id"] = gist_id
                print_success(f"已创建新的私密 Gist：{gist_id} 喵！")
    else:
        # 用户没有提供 Gist ID，创建新的
        gist_id = _create_new_gist(token, file_name)
        cfg["gist_id"] = gist_id
        print_success(f"已创建新的私密 Gist：{gist_id} 喵！")
    
    _save_gist_config(cfg)
    return cfg

def _ensure_gist_config():
    cfg = _load_gist_config()
    if cfg is None:
        cfg = _setup_gist_interactive()
    return cfg

def append_scr_path(relative_path):
    return os.path.join(CONFIG_DIR, relative_path)


def str2bool(value):
    if isinstance(value, bool):
        return value
    lowered = value.strip().lower()
    if lowered in ("true", "t", "1", "yes", "y"):
        return True
    if lowered in ("false", "f", "0", "no", "n"):
        return False
    raise argparse.ArgumentTypeError("flatten-single-file 期望布尔值喵（true/false）")


def parse_cli_arguments(argv):
    parser = argparse.ArgumentParser(
        prog="auto_decompression",
        description="自动解压压缩包并支持嵌入内容提取的小工具喵",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-e",
        "--embedded-scan-depth",
        type=int,
        default=DEFAULT_EMBEDDED_SCAN_MAX_LEVEL,
        metavar="K",
        help="在递归层级小于等于 K 时尝试检测及提取隐藏嵌入文件，设为 0 可禁用此功能",
    )
    parser.add_argument(
        "--flatten-single-file",
        type=str2bool,
        default=True,
        metavar="{true,false}",
        help="检测到仅包含与压缩包同名的单个文件时是否自动扁平化喵（默认 true）。",
    )
    parser.add_argument(
        "--trash-on-success",
        type=str2bool,
        default=True,
        metavar="{true,false}",
        help="当（递归）解压成功时，将被解压的原始压缩文件（含分卷）移动到回收站喵（默认 true）。"
    )
    parser.add_argument(
        "--config-dir",
        type=str,
        default=None,
        help="指定配置文件（不含密码字典）的所在文件夹喵，密码字典将存放在数据目录中。",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="需解压的压缩文件路径，可直接拖拽物件到脚本上喵",
    )
    return parser.parse_args(argv)


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


def read_passwords():
    global pwdDictionary
    pwdPath = os.path.join(DATA_DIR, pwdFilename)
    try:
        with open(pwdPath, "r", encoding="utf-8") as file:
            pwdDictionary = json.load(file)
    except Exception as e:
        print_warning(f"读取文件错误喵！错误信息：{e}")


def save_passwords():
    # print(str(pwdDictionary))
    pwdPath = os.path.join(DATA_DIR, pwdFilename)
    try:
        with open(pwdPath, "w", encoding="utf-8") as file:
            json.dump(pwdDictionary, file, ensure_ascii=False, indent=4)
        # print(f"密码已成功保存到 {pwdPath} 喵～")
    except Exception as e:
        print_warning(f"保存密码时出错喵！请检查文件权限或路径。错误信息：{e}")


def _pull_from_gist_if_possible():
    """在本地文件缺失的情况下尝试从 Gist 拉取密码本，成功返回 True"""
    global _gist_cfg, _gist_remote_ts, pwdDictionary
    if _gist_cfg is None:
        return False
    remote_dict, ts = _fetch_from_gist(_gist_cfg)
    if remote_dict is not None:
        pwdDictionary = remote_dict
        _gist_remote_ts = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
        save_passwords()
        print_success("已从 Gist 拉取密码本喵！")
        return True
    return False


def check_passwords():
    global pwdDictionary
    pwdPath = os.path.join(DATA_DIR, pwdFilename)
    if not os.path.exists(pwdPath):
        _ensure_directory(DATA_DIR, "数据")
        legacy_paths = (
            os.path.join(CONFIG_DIR, pwdFilename),
            os.path.join(sys.path[0], pwdFilename),
        )
        for legacy_path in legacy_paths:
            if not os.path.exists(legacy_path):
                continue
            try:
                with open(legacy_path, "r", encoding="utf-8") as file:
                    legacy_data = json.load(file)
                if not isinstance(legacy_data, dict):
                    raise ValueError("密码本格式无效")
            except Exception as e:
                print_warning(f"旧密码本格式异常，跳过迁移喵：{e}")
                continue
            try:
                shutil.copy2(legacy_path, pwdPath)
                print_info(f"已将旧密码本迁移到新的数据目录喵：{legacy_path}")
            except Exception as e:
                print_warning(f"迁移旧密码本失败喵：{e}")
            break
    if not os.path.exists(pwdPath):
        # 若本地缺失则优先尝试从 Gist 获取
        if _pull_from_gist_if_possible():
            return
        pwdDictionary = {}
    else:
        read_passwords()


def _sync_to_gist_before_exit():
    global _gist_cfg, _gist_remote_ts
    if _gist_cfg is None:
        return
    pwdPath = os.path.join(DATA_DIR, pwdFilename)
    if not os.path.exists(pwdPath):
        return
    local_mtime = _dt.datetime.fromtimestamp(os.path.getmtime(pwdPath), tz=_dt.timezone.utc)
    remote_dict, remote_ts_str = _fetch_from_gist(_gist_cfg)
    if remote_ts_str:
        remote_ts = _dt.datetime.fromisoformat(remote_ts_str.replace("Z", "+00:00"))
    else:
        remote_ts = None

    if remote_ts and _gist_remote_ts and remote_ts > _gist_remote_ts and remote_ts > local_mtime:
        print_warning("检测到远程密码本在本次会话期间发生更新，可能与本地冲突喵！")
        print_warning(f"远程最后更新时间：{remote_ts.isoformat()} 本地最后更新时间：{local_mtime.isoformat()}")
        # 冲突时依旧继续上传由用户自行决定，示例中选择继续

    if _update_gist(_gist_cfg, json.dumps(pwdDictionary, ensure_ascii=False, indent=4)):
        print_success("已同步密码本到 Gist 喵！")
    else:
        print_warning("同步到 Gist 失败喵，请稍后重试！")
    time.sleep(1)

# 注册到 atexit，以便任何正常退出路径都会尝试同步
atexit.register(_sync_to_gist_before_exit)

def add_password(pwd, count=1):
    if pwd == None:
        return
    if pwd in pwdDictionary:
        pwdDictionary[pwd] += count
    else:
        pwdDictionary[pwd] = count


import re
import threading


def get_total_split_size(file_path: str) -> int:
    """Calculates combined size of all parts in a multi-volume archive."""
    dir_name = os.path.dirname(file_path)
    base_name = os.path.basename(file_path)

    # 如果没有 '.'，直接返回当前文件大小
    if "." not in base_name:
        return os.path.getsize(file_path)

    # 找到所有 '.' 的位置
    dot_positions = [i for i, c in enumerate(base_name) if c == '.']
    if len(dot_positions) == 1:
        base_part = base_name[:dot_positions[0]]
    else:
        base_part = base_name[:dot_positions[-2]]

    total_size = 0
    for fname in os.listdir(dir_name or "."):
        full_path = os.path.join(dir_name, fname)
        if not os.path.isfile(full_path):
            continue

        # 对比相同策略所得的前缀
        cur_dots = [i for i, c in enumerate(fname) if c == '.']
        if not cur_dots:
            continue
        elif len(cur_dots) == 1:
            cur_base_part = fname[:cur_dots[0]]
        else:
            cur_base_part = fname[:cur_dots[-2]]

        if cur_base_part == base_part:
            total_size += os.path.getsize(full_path)

    return total_size


def extract_with_7zip(file_path, extract_to, password: str = None):
    """Extracts archive using 7-Zip with real-time progress reporting."""
    command = ["7z", "x", file_path, f"-o{extract_to}", "-y", "-bsp1", "-bb3", "-sccUTF-8"]
    if password:
        command.extend(["-p" + password])

    # 启动7z进程
    # Use explicit UTF-8 encoding for stdout/stderr to avoid crashes on Windows (defaulting to GBK)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    task = -1
    last_percent = 0
    file_size = get_total_split_size(file_path)
    result = 1
    err_log = ""

    # 定义处理 stdout 的函数
    def handle_stdout():
        nonlocal last_percent, task, result
        for line in iter(process.stdout.readline, ""):
            if process.poll() is not None:
                break  # 进程已经结束
            line = line.strip()
            # print(": " + line)
            if "- " in line:
                current_file = line.split("- ", 1)[1].replace("\\", "/")
                progress.update(task, filename=current_file, refresh=True)
            if "%" in line:
                match = re.search(r"(\d+)%", line)
                if match:
                    percent = int(match.group(0).replace("%", ""))
                    progress_increment = (
                        int((percent - last_percent) * file_size / 100) + 1
                    )
                    progress.update(task, advance=progress_increment, refresh=True)
                    last_percent = percent
            if "Everything is Ok" in line:
                progress.update(
                    task, advance=int(file_size - last_percent * file_size / 100) + 1
                )
                progress.refresh()

    # 定义处理 stderr 的函数
    def handle_stderr():
        nonlocal result
        nonlocal err_log
        for err_line in iter(process.stderr.readline, ""):
            err_line = err_line.strip()
            err_log += err_line + "\n"
            # print_error(f"\n{err_line}\n")
            if err_line:
                process.terminate()
                # 检查错误信息
                if "wrong password" in err_line.lower():
                    result = -1
                    break
                elif "cannot open" in err_line.lower():
                    result = -2
                    break
                else:
                    result = -3

    # 实时输出进度
    with Progress(
        rich.progress.SpinnerColumn(finished_text="✅"),
        rich.progress.TextColumn(
            "[cyan][b]{task.fields[filename]}[/cyan][/b]",
            table_column=rich.progress.Column(max_width=75),
        ),
        rich.progress.BarColumn(),
        "[progress.percentage]{task.percentage:>3.1f}%",
        "•",
        rich.progress.FileSizeColumn(),
        "•",
        rich.progress.TransferSpeedColumn(),
        "•",
        rich.progress.TimeElapsedColumn(),
        "/",
        rich.progress.TimeRemainingColumn(),
        transient=True,
    ) as progress:

        task = progress.add_task("Decompress...", total=file_size, filename="")

        # 启动线程来处理 stdout 和 stderr
        stdout_thread = threading.Thread(target=handle_stdout)
        stderr_thread = threading.Thread(target=handle_stderr)

        stdout_thread.start()
        stderr_thread.start()

        # 等待线程完成
        stdout_thread.join()
        stderr_thread.join()

    if result == -1:
        print_info(f"密码 {password} 尝试错误喵。")
    elif result == -2:
        print_info(f"{file_path}\n可能不是压缩文件喵。")
    elif result == -3:
        print_warning(f"未定义错误（可能是密码错误喵）。错误日志：\n{err_log}")
    else:
        print_success("解压完成，没有错误喵。")

    return result


def extract_with_bandizip(file_path, extract_to, password=None):
    """
    Fallback extraction using Bandizip CLI (bz.exe).
    
    Returns:
        int: 1 for success, -1 for wrong password, -2 for invalid file, -3 for other errors.
    """
    # 构建命令，避免在参数内使用引号，让subprocess处理路径
    command = ["bz", "x", f"-o:{extract_to}", "-aoa", "-y"]
    if password:
        command.append(f"-p:{password}")
    command.append(file_path)

    # Get directory contents before extraction
    try:
        before_files = set(os.listdir(extract_to))
    except FileNotFoundError:
        before_files = set()

    # 执行 Bandizip 解压
    process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='ignore')
    
    # 分析错误输出以确定具体错误类型
    stderr_output = process.stderr.lower()
    stdout_output = process.stdout.lower()
    combined_output = stderr_output + stdout_output
    
    # 检查是否为密码错误
    if "invalid password" in combined_output or "0xa0000021" in combined_output:
        return -1
    
    # 检查是否为无法打开文件的错误
    if ("cannot open" in combined_output or 
        "Unknown archive" in combined_output or 
        "unsupported" in combined_output or
        "corrupted" in combined_output or
        "系统找不到指定的文件" in combined_output or
        "file not found" in combined_output):
        return -2

    # Get directory contents after extraction
    try:
        after_files = set(os.listdir(extract_to))
    except FileNotFoundError:
        after_files = set()

    new_files = after_files - before_files

    if not new_files:
        # 如果返回码不为0且没有新文件，可能是其他错误
        if process.returncode != 0:
            # 输出详细的错误信息
            print_error("Bandizip 执行出现未知错误喵：")
            print_error(f"命令: {' '.join(command)}")
            print_error(f"返回码: {process.returncode}")
            if process.stdout.strip():
                print_error(f"标准输出: {process.stdout}")
            if process.stderr.strip():
                print_error(f"错误输出: {process.stderr}")
            return -3
        return -2  # 没有新文件，可能不是压缩文件

    # Check if any new file has size > 0
    for item in new_files:
        full_item_path = os.path.join(extract_to, item)
        if os.path.isfile(full_item_path) and os.path.getsize(full_item_path) > 0:
            return 1  # 成功
        if os.path.isdir(full_item_path):
            for root, _, files_in_dir in os.walk(full_item_path):
                for f_in_dir in files_in_dir:
                    if os.path.getsize(os.path.join(root, f_in_dir)) > 0:
                        return 1  # 成功

    # 解压了文件但都是空文件，可能是某种错误
    print_error("Bandizip 解压了文件但都是空文件，出现未知错误喵：")
    print_error(f"命令: {' '.join(command)}")
    print_error(f"返回码: {process.returncode}")
    if process.stdout.strip():
        print_error(f"标准输出: {process.stdout}")
    if process.stderr.strip():
        print_error(f"错误输出: {process.stderr}")
    return -3


def handle_bandizip_extraction(file_path, temp_folder, passwords, level):
    """
    使用 Bandizip 处理解压，会遍历密码字典并支持手动输入。
    成功则返回密码，失败则返回 None。
    """
    print_info("7zip 打不开这个提取出来的文件，换用 Bandizip 试试喵...")
    # 1. 尝试密码字典中的所有密码
    for pwd_item in passwords:
        pwd = pwd_item[0]
        result = extract_with_bandizip(file_path, temp_folder, pwd)
        if result == 1:  # 成功
            print_success(f"Bandizip 使用密码 '{pwd}' 解压成功喵！")
            return pwd
        elif result == -1:  # 密码错误，继续尝试下一个
            print_info(f"密码 '{pwd}' 错误喵。")
        elif result == -2:  # 无法打开文件
            print_warning("Bandizip 无法打开此文件，可能不是压缩文件或文件已损坏喵。")
            return None
        else:  # 其他错误，不再继续尝试
            print_warning("Bandizip 遇到未知错误，停止尝试喵。")
            return None

    # 2. 如果字典密码都失败了，请求手动输入
    while True:
        console.print(f"[cyan][b]（Bandizip）请输入第{level}层文件的解压密码喵：", end="")
        password = input()
        if not password:  # 用户直接回车，取消操作
            print_warning(f"用户跳过了文件 {file_path} 的手动密码输入喵，将跳过该文件。")
            return None
        
        result = extract_with_bandizip(file_path, temp_folder, password)
        if result == 1:  # 成功
            return password
        elif result == -1:  # 密码错误
            print_warning("密码错误，请重新输入喵！")
        elif result == -2:  # 无法打开文件
            print_warning("Bandizip 无法打开此文件，可能不是压缩文件或文件已损坏喵。")
            return None
        else:  # 其他错误，不再继续尝试
            print_warning("Bandizip 遇到未知错误，停止尝试喵。")
            return None


def try_passwords(file_path, extract_to, passwords, last_tried_password):
    """Iterates through dictionary passwords to find a match."""
    for password in passwords:
        password = password[0]
        if last_tried_password == password:
            continue
        if extract_with_7zip(file_path, extract_to, password) > 0:
            return password
    return None


def manual_password_entry(file_path, extract_to, level):
    """Prompts user for password entry when dictionary lookup fails."""
    while True:
        console.print(f"[cyan][b]请输入第{level}层文件的解压密码喵：", end="")
        password = input()
        if password == "":
            print_warning(f"用户跳过了文件 {file_path} 的手动密码输入喵，将跳过该文件。")
            return None
        if extract_with_7zip(file_path, extract_to, password) > 0:
            return password
        print_warning("密码错误，请重新输入喵！")


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
    Returns a list of primary files (first volume of a set or regular files).
    """
    files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
    
    # Regex for different archive parts
    # 1. .part1.rar, .part01.rar, .part001.rar etc.
    # 2. .r00, .r01, ...
    # 3. .7z.001, .zip.001, ...
    # 4. .z01, .z02, ... (first file is .zip)
    part_regex = re.compile(r'(.+)\.part\d+\.rar$', re.IGNORECASE)
    r_regex = re.compile(r'(.+)\.r\d+$', re.IGNORECASE)
    num_regex = re.compile(r'(.+)\.(7z|zip)\.\d+$', re.IGNORECASE)
    z_regex = re.compile(r'(.+)\.z\d+$', re.IGNORECASE)

    archives = {} # key: base_name, value: list of parts
    
    # First pass: group all volume parts
    for f in files:
        match = part_regex.match(f) or r_regex.match(f) or num_regex.match(f) or z_regex.match(f)
        if match:
            base_name = match.group(1)
            if base_name not in archives:
                archives[base_name] = []
            archives[base_name].append(f)

    # Identify primary files
    primary_files = []
    processed_files = set()

    # Add the first part of each archive group
    for base_name, parts in archives.items():
        parts.sort() # Sort to find the first part reliably
        first_part = parts[0]
        
        # For .z01 style, the .zip file is the primary
        if z_regex.match(first_part):
            zip_file = f"{base_name}.zip"
            if zip_file in files:
                primary_files.append(zip_file)
                processed_files.add(zip_file)
            else: # If .zip is missing, use the first part
                primary_files.append(first_part)
        else:
            primary_files.append(first_part)
        
        processed_files.update(parts)

    # Add all other non-volume files
    for f in files:
        if f not in processed_files:
            primary_files.append(f)
            
    return primary_files


global_last_success_password = None


def try_remove_directory(dir):
    try:
        shutil.rmtree(dir)
    except:
        pass


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


def recursive_extract(
    base_folder,
    file_path,
    last_success_password=None,
    level=1,
    embedded_scan_depth=DEFAULT_EMBEDDED_SCAN_MAX_LEVEL,
    source_archive_paths: set = None,
):
    """Recursively extracts archives, handling nested compressed files and passwords."""
    global global_last_success_password
    global extract_to_base_folder
    global auto_flatten_single_file

    source_archive_paths = set(source_archive_paths or [])

    temp_folder = create_unique_directory(base_folder, "temp_extract")
    orig_temp_folder = temp_folder  # 保存最初创建的临时目录路径
    last_compressed_file_name = get_archive_base_name(file_path)

    passwords = sorted(pwdDictionary.items(), key=lambda item: item[1], reverse=True)
    password = (
        last_success_password if last_success_password is not None else passwords[0][0]
    )

    while True:
        tryResult = extract_with_7zip(file_path, temp_folder, password)
        if tryResult == -1:
            # Try dictionary passwords first (excluding current)
            next_password = try_passwords(
                file_path, temp_folder, passwords, password
            )
            # Try archive name as a password fallback
            if next_password is None:
                name_pwd = last_compressed_file_name
                if name_pwd and name_pwd != password:
                    print_info(
                        f"Trying archive name '{name_pwd}' as password..."
                    )
                    if extract_with_7zip(file_path, temp_folder, name_pwd) > 0:
                        next_password = name_pwd
            # Request manual entry if all automated attempts fail
            if next_password is None:
                next_password = manual_password_entry(file_path, temp_folder, level)
            if next_password is None:
                print_warning(f"用户跳过了文件 {file_path} 的密码输入喵，将跳过该文件。")
                try_remove_directory(orig_temp_folder)
                return True
            password = next_password
            break
        elif tryResult == -2:
            # 7-Zip cannot open file; attempt alternative methods
            if file_path.endswith(RECOVER_SUFFIX):
                # Files extracted from hidden segments might require Bandizip
                new_password = handle_bandizip_extraction(file_path, temp_folder, passwords, level)
                if new_password:
                    password = new_password # 更新当前密码
                    break  # Bandizip成功，跳出while循环，继续后续处理
                else:
                    # Bandizip也失败了，这个文件没救了
                    print_warning("Bandizip 也无法处理这个文件喵。")
                    try_remove_directory(orig_temp_folder)
                    return True # 结束当前分支的解压

            # Search for embedded hidden archives if standard opening fails
            found_embedded = False
            for fmt in ["zip", "rar", "7z", "*"]:
                if (
                    level <= embedded_scan_depth
                    and RECOVER_SUFFIX not in file_path
                    and hiddenZip.has_embedded_signature(file_path, fmt)
                ):
                    print_info(
                        f"Found embedded {fmt.upper() if fmt != '*' else 'file'}, extracting..."
                    )
                    hiddenZip.extract_embedded_file(
                        file_path, file_path + RECOVER_SUFFIX, fmt
                    )
                    file_path = file_path + RECOVER_SUFFIX
                    found_embedded = True
                    break
            
            if found_embedded:
                continue

            # 如果以上所有尝试都失败了
            try_remove_directory(orig_temp_folder)
            return True
        else:
            break

    global_last_success_password = password
    add_password(password)

    # Scan the temporary folder for files and group multi-volume archives
    try:
        grouped_files = group_archive_files(temp_folder)
        # If the only item is a directory, go deeper.
        while len(grouped_files) == 0 and len(os.listdir(temp_folder)) == 1:
            only_item_name = os.listdir(temp_folder)[0]
            deeper_folder = os.path.join(temp_folder, only_item_name)
            if os.path.isdir(deeper_folder):
                temp_folder = deeper_folder
                last_compressed_file_name = os.path.basename(temp_folder)
                grouped_files = group_archive_files(temp_folder)
            else:
                break  # Not a directory, stop digging
    except FileNotFoundError:
        # This can happen if extraction yields an empty folder that gets deleted.
        grouped_files = []

    # 在判定是否继续递归时，忽略用于混淆的小体积“非压缩文件”喵
    filtered_grouped_files = []
    for fname in grouped_files:
        full_path = os.path.join(temp_folder, fname)
        if not os.path.isfile(full_path):
            continue

        # 只对“看起来不像压缩包”的文件做体积阈值过滤
        if not is_likely_archive_filename(fname):
            try:
                size = os.path.getsize(full_path)
            except OSError:
                size = SMALL_NON_ARCHIVE_IGNORE_THRESHOLD + 1

            if size <= SMALL_NON_ARCHIVE_IGNORE_THRESHOLD:
                # 这是一个小体积的非压缩文件，用于混淆时可以直接忽略喵
                continue

        filtered_grouped_files.append(fname)

    grouped_files = filtered_grouped_files

    finished = False
    if len(grouped_files) == 1:
        new_file_path = os.path.join(temp_folder, grouped_files[0])
        finished = recursive_extract(
            base_folder,
            new_file_path,
            password,
            level + 1,
            embedded_scan_depth=embedded_scan_depth,
            source_archive_paths=source_archive_paths,
        )
        if not finished:
            try:
                os.remove(new_file_path)
            except:
                pass
    else:
        finished = True

    if finished:
        allow_replace_reserved = bool(
            source_archive_paths
            and CLI_ARGS is not None
            and getattr(CLI_ARGS, "trash_on_success", False)
        )

        flattened_output_path = None
        try:
            temp_entries = os.listdir(temp_folder)
        except FileNotFoundError:
            temp_entries = []

        if auto_flatten_single_file and not extract_to_base_folder and temp_entries:
            single_file_path = detect_single_same_named_file(
                temp_folder,
                last_compressed_file_name,
                entries=temp_entries,
            )
            if single_file_path:
                flattened_output_path = move_file_with_unique_suffix(
                    single_file_path,
                    base_folder,
                    reserved_paths=source_archive_paths,
                    allow_replace_reserved=allow_replace_reserved,
                )
                print_success(
                    f"检测到 {last_compressed_file_name}/"
                    f"{os.path.basename(flattened_output_path)} 结构喵，"
                    f"已直接将文件放置到目标目录：{flattened_output_path}"
                )

        if not flattened_output_path:
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
                desired_target_folder = os.path.join(
                    base_folder, last_compressed_file_name
                )
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
                    target_folder = create_unique_directory(
                        base_folder, last_compressed_file_name
                    )

                for entry in temp_entries:
                    shutil.move(os.path.join(temp_folder, entry), target_folder)

                if needs_reserved_replacement:
                    final_target_folder = target_folder
                    try:
                        send2trash.send2trash(desired_target_folder)
                        os.rename(target_folder, desired_target_folder)
                        final_target_folder = desired_target_folder
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

    try_remove_directory(orig_temp_folder)
    return False


SERVER_PORT = 65432

from multiprocessing import Process, Manager
import time
from filelock import FileLock, Timeout

# Function to send file path to the main instance
queue_file_path = append_scr_path("queue_file.txt")
queue_file_lock = append_scr_path("queue_file.lock")
instance_lock = append_scr_path("instance.lock")


def send_file_to_main_instance(file_paths):
    print(str(file_paths))
    lock = FileLock(queue_file_lock)
    with lock.acquire(timeout=-1):
        with open(queue_file_path, "a", encoding="utf-8") as f:
            for file in file_paths:
                f.write(file + "\n")


class FileManager:
    def __init__(self, queue_path, lock_path):
        self.queue_path = queue_path
        self.lock_path = lock_path
        manager = Manager()
        self.files_to_process = manager.list()
        self.process = Process(target=self.queue_listener)
        self.process.start()

    def queue_listener(self):
        while True:
            lock = FileLock(self.lock_path)
            if os.path.exists(self.queue_path):
                with lock.acquire(timeout=0):
                    with open(self.queue_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    if lines:
                        os.remove(self.queue_path)
                        for line in lines:
                            self.files_to_process.append(line.strip())
            time.sleep(0.1)

    def stop(self):
        self.process.terminate()

    def __del__(self):
        self.stop()


def main(args):
    global extract_to_base_folder, _gist_cfg, _gist_remote_ts, embedded_scan_depth_setting, auto_flatten_single_file

    _gist_cfg = _ensure_gist_config()

    manager = FileManager(queue_file_path, queue_file_lock)

    check_passwords()

    embedded_scan_depth_setting = max(0, args.embedded_scan_depth)
    auto_flatten_single_file = args.flatten_single_file
    if not auto_flatten_single_file:
        print_info("已禁用同名单文件自动扁平化喵。")
    if args.embedded_scan_depth < 0:
        print_warning("嵌入检测层级小于 0 喵，已自动调整为 0（禁用嵌入扫描）。")
    if embedded_scan_depth_setting == 0:
        print_info("当前已禁用隐藏嵌入文件判定喵。")
    elif embedded_scan_depth_setting != DEFAULT_EMBEDDED_SCAN_MAX_LEVEL:
        print_info(
            f"隐藏嵌入文件判定最大层级已调整为 {embedded_scan_depth_setting} 层喵。"
        )

    files_to_process = list(args.files)

    try:
        if len(files_to_process) > 0:
            console.print(
                "[cyan][b]要为每个压缩包单独建立一个文件夹吗？[Y/n]：", end=""
            )
            question = input()
            if question.lower() == "n":
                print_info("将所有压缩包内的文件都解压到当前文件夹下喵❤")
                extract_to_base_folder = True
            while True:
                current_batch = files_to_process[:]
                files_to_process = []
                for file_path in current_batch:
                    if file_path.lower().endswith(".apk"):
                        print_info(f"跳过 .apk 文件：{file_path} 喵。")
                        continue
                    print_info(f"开始解压文件 {file_path} 喵❤")
                    _RECYCLED_RESERVED_PATHS.clear()
                    base_folder = os.path.dirname(file_path)
                    if move_temp_folders_to_recycle_bin(base_folder):
                        print_info(
                            "检测到上一次非正常退出留下的临时文件夹喵！已经把它们全部移动到回收站了喵☆"
                        )

                    try:
                        source_archive_paths = {
                            _normalize_path_for_compare(p)
                            for p in list_related_archive_parts(file_path)
                        }
                    except Exception:
                        source_archive_paths = {_normalize_path_for_compare(file_path)}

                    _ret = recursive_extract(
                        base_folder,
                        file_path,
                        global_last_success_password,
                        embedded_scan_depth=embedded_scan_depth_setting,
                        source_archive_paths=source_archive_paths,
                    )
                    # 解压成功才执行回收站移动；失败（非密码错误导致）则不移动
                    if _ret is False and hasattr(CLI_ARGS, "trash_on_success") and CLI_ARGS.trash_on_success:
                        try:
                            for p in list_related_archive_parts(file_path):
                                if _normalize_path_for_compare(p) in _RECYCLED_RESERVED_PATHS:
                                    # This path now points to extracted output after a reserved-name replacement.
                                    continue
                                if os.path.exists(p):
                                    send2trash.send2trash(p)
                                    print_info(f"已将被解压的原始压缩文件移动到回收站：{p}")
                        except Exception as e:
                            print_warning(f"移动原始压缩文件到回收站失败喵：{e}")
                    remove_autodec_files(base_folder)
                save_passwords()  # 保存到本地
                if not manager.files_to_process:
                    break
                files_to_process.extend(manager.files_to_process)
                manager.files_to_process[:] = []  # 使用切片赋值清空列表
            print_info("解压完成，退出程序喵...")
        else:
            print_warning("请拖拽一个文件到这个脚本上进行解压喵！")
            print_info("也可以输入想要添加的密码喵：")
            while True:
                pwd = input()
                if pwd != "":
                    add_password(pwd, 0)
                    save_passwords()
                    print_info(f"已添加密码 {pwd} 喵！")
                else:
                    break
    except Exception as e:
        error_end(e)


def error_end(e: Exception = None):
    print_error(
        f"程序出现错误喵>.< 非常抱歉喵，下面是错误信息喵！\n{traceback.format_exc()}"
    )
    input()


if __name__ == "__main__":
    CLI_ARGS = parse_cli_arguments(sys.argv[1:])

    if CLI_ARGS.config_dir:
        CONFIG_DIR = os.path.abspath(CLI_ARGS.config_dir)
        # 更新依赖 CONFIG_DIR 的全局路径变量
        queue_file_path = append_scr_path("queue_file.txt")
        queue_file_lock = append_scr_path("queue_file.lock")
        instance_lock = append_scr_path("instance.lock")

    _ensure_directory(CONFIG_DIR, "配置")
    _ensure_directory(DATA_DIR, "数据")

    try:
        lock = FileLock(instance_lock)
        with lock.acquire(timeout=0):
            try:
                main(CLI_ARGS)
                time.sleep(1)
            except Exception as e:
                error_end(e)
    except Timeout:
        if CLI_ARGS.files:
            # Try to send file paths to the existing instance
            send_file_to_main_instance(CLI_ARGS.files)
        if CLI_ARGS.embedded_scan_depth != DEFAULT_EMBEDDED_SCAN_MAX_LEVEL:
            print_warning(
                "已有实例正在运行，新的嵌入扫描层级参数未被应用喵。请先关闭原实例再重新运行。"
            )
        print_info("检测到已经有一个实例在运行，已将任务添加到队列中喵！")
        pass
    except Exception:
        error_end()
