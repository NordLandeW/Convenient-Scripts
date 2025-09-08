import json
import subprocess
import os
import sys
import shutil
import traceback
import extract_hidden_zip as hiddenZip
import send2trash
from rich.console import Console
from rich.progress import Progress
import rich.progress
import requests
import datetime as _dt
import atexit
import time

__version__ = "1.1.0"
console = Console()
extract_to_base_folder = False
pwdOldFilename = "dict.txt"
pwdFilename = "dict.json"
pwdDictionary = {}
RECOVER_SUFFIX = ".AutoDecRecovered"

GIST_CONFIG_FILE = "gist_config.json"
_gist_cfg = None  # {token:str, gist_id:str, file:str}
_gist_remote_ts = None  # 上一次拉取时远程文件 updated_at（datetime）


def _cfg_path(fname):
    return os.path.join(sys.path[0], fname)

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
    """返回 (dict, gist_updated_at str) 或 (None, None)"""
    try:
        r = requests.get(
            f"https://api.github.com/gists/{cfg['gist_id']}",
            headers=_gist_headers(cfg['token']),
        )
        if r.status_code == 200:
            gist = r.json()
            file_info = gist["files"].get(cfg["file"])
            if file_info and file_info.get("content") is not None:
                # ↳ 文件对象本身没有 updated_at，使用 gist 的 updated_at 字段
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
        local_pwd_path = os.path.join(sys.path[0], pwdFilename)
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
    return os.path.join(sys.path[0], relative_path)


def print_info(message):
    """用蓝色打印普通信息喵"""
    console.out(message, style="blue")


def print_error(message):
    """用红色加粗打印错误信息喵"""
    console.out(message, style="bold red")


def print_success(message):
    """用绿色打印成功信息喵"""
    console.out(message, style="green")


def print_warning(message):
    """用黄色打印警告信息喵"""
    console.out(message, style="bold yellow underline")


def move_temp_folders_to_recycle_bin(current_directory):
    # 获取当前目录下的所有文件和文件夹
    items = os.listdir(current_directory)

    # 过滤出以 'temp_extract' 为前缀的子文件夹
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
    """创建一个唯一的目录，如果目录存在，则添加波浪号来避免重复喵"""
    counter = 1
    original_dir_name = dir_name
    while os.path.exists(os.path.join(base_path, dir_name)):
        dir_name = f"{original_dir_name}~{counter}"
        counter += 1
    os.makedirs(os.path.join(base_path, dir_name))
    print_success(f"创建目录：{dir_name}")
    return os.path.join(base_path, dir_name)


def read_passwords_old():
    """从与脚本同一目录下的dict.txt中读取密码喵，如果文件不存在或为空则返回空列表"""
    passwords = []
    pwdPath = os.path.join(sys.path[0], pwdOldFilename)
    try:
        with open(pwdPath, "r", encoding="utf-8") as file:
            passwords = file.read().splitlines()
    except Exception as e:
        print_warning(f"读取旧密码文件错误喵！错误信息：{e}")

    return passwords if len(passwords) > 0 else ["???"]


# Old version workaround.
def convert_old_pwd_to_new_pwd(passwords):
    for pwd in passwords:
        pwdDictionary[pwd] = 0
    save_passwords()
    print_info("密码本格式更新完毕喵！")


def read_passwords():
    global pwdDictionary
    pwdPath = os.path.join(sys.path[0], pwdFilename)
    try:
        with open(pwdPath, "r", encoding="utf-8") as file:
            pwdDictionary = json.load(file)
    except Exception as e:
        print_warning(f"读取文件错误喵！错误信息：{e}")


def save_passwords():
    # print(str(pwdDictionary))
    pwdPath = os.path.join(sys.path[0], pwdFilename)
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
    pwdPath = os.path.join(sys.path[0], pwdFilename)
    pwdOldPath = os.path.join(sys.path[0], pwdOldFilename)
    if not os.path.exists(pwdPath):
        # 若本地缺失则优先尝试从 Gist 获取
        if _pull_from_gist_if_possible():
            return
        # Check if a old version file exists.
        if os.path.exists(pwdOldPath):
            convert_old_pwd_to_new_pwd(read_passwords_old())
        else:
            pwdDictionary = {}
    else:
        read_passwords()


def _sync_to_gist_before_exit():
    global _gist_cfg, _gist_remote_ts
    if _gist_cfg is None:
        return
    pwdPath = os.path.join(sys.path[0], pwdFilename)
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
    """
    取倒数第二个（如果只有一个'.'就取一个）'.'之前相同的部分，计算所有同样前缀文件的总大小。
    """
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
    """使用7zip尝试解压文件到指定目录，可能需要密码，并实时显示美观的进度条喵"""
    command = ["7z", "x", file_path, f"-o{extract_to}", "-y", "-bsp1", "-bb3"]
    if password:
        command.extend(["-p" + password])

    # 启动7z进程
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
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
    """使用 Bandizip 命令行版本 (bz.exe) 尝试解压文件喵
    
    返回值：
    1: 成功解压
    -1: 密码错误
    -2: 无法打开文件（不是压缩文件或文件损坏）
    -3: 其他错误
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
            print_warning("用户取消了手动输入密码。")
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
    """尝试一系列密码解压文件喵，如果没有有效密码则返回None"""
    for password in passwords:
        password = password[0]
        if last_tried_password == password:
            continue
        if extract_with_7zip(file_path, extract_to, password) > 0:
            return password
    return None


def manual_password_entry(file_path, extract_to, level):
    """当字典中的密码都无效时，手动请求用户输入密码喵"""
    while True:
        console.print(f"[cyan][b]请输入第{level}层文件的解压密码喵：", end="")
        password = input()
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


def recursive_extract(base_folder, file_path, last_success_password=None, level=1):
    global global_last_success_password
    global extract_to_base_folder
    """递归解压文件，处理密码保护的压缩文件喵"""
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
            password = try_passwords(
                file_path, temp_folder, passwords, password
            ) or manual_password_entry(file_path, temp_folder, level)
            break
        elif tryResult == -2:
            # 7zip 无法打开文件，尝试其他方法
            if file_path.endswith(RECOVER_SUFFIX):
                # 这是一个从隐藏zip中提取的文件，7zip打不开，需要用Bandizip处理
                new_password = handle_bandizip_extraction(file_path, temp_folder, passwords, level)
                if new_password:
                    password = new_password # 更新当前密码
                    break  # Bandizip成功，跳出while循环，继续后续处理
                else:
                    # Bandizip也失败了，这个文件没救了
                    print_warning("Bandizip 也无法处理这个文件喵。")
                    try_remove_directory(orig_temp_folder)
                    return True # 结束当前分支的解压

            # 如果不是RECOVER_SUFFIX文件，或者Bandizip失败了，走原来的隐藏文件检测逻辑
            found_embedded = False
            for fmt in ["zip", "rar", "7z", "*"]:
                if level <= 2 and RECOVER_SUFFIX not in file_path and hiddenZip.has_embedded_signature(file_path, fmt):
                    print_info(
                        f"发现文件嵌入了隐藏{"的某个文件" if fmt == "*" else fmt.upper()}喵，准备处理喵！"
                    )
                    hiddenZip.extract_embedded_file(
                        file_path, file_path + RECOVER_SUFFIX, fmt
                    )
                    file_path = file_path + RECOVER_SUFFIX
                    found_embedded = True
                    break # 跳出 for 循环，进入下一个 while 循环再次尝试解压
            
            if found_embedded:
                continue # 重新开始 while 循环，用 7zip 尝试解压新提取的文件

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
                break # Not a directory, stop digging
    except FileNotFoundError:
        # This can happen if extraction yields an empty folder that gets deleted.
        grouped_files = []

    finished = False
    if len(grouped_files) == 1:
        new_file_path = os.path.join(temp_folder, grouped_files[0])
        finished = recursive_extract(base_folder, new_file_path, password, level + 1)
        if not finished:
            try:
                os.remove(new_file_path)
            except:
                pass
    else:
        finished = True

    if finished:
        if extract_to_base_folder:
            target_folder = base_folder
        else:
            target_folder = create_unique_directory(
                base_folder, last_compressed_file_name
            )
        
        # Move all contents from the final temp_folder to target
        all_contents = os.listdir(temp_folder)
        for f in all_contents:
            shutil.move(os.path.join(temp_folder, f), target_folder)
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
    def __init__(self):
        manager = Manager()
        self.files_to_process = manager.list()
        self.process = Process(target=self.queue_listener)
        self.process.start()

    def queue_listener(self):
        while True:
            lock = FileLock(queue_file_lock)
            if os.path.exists(queue_file_path):
                with lock.acquire(timeout=0):
                    with open(queue_file_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    if lines:
                        os.remove(queue_file_path)
                        for line in lines:
                            self.files_to_process.append(line.strip())
            time.sleep(0.1)

    def stop(self):
        self.process.terminate()

    def __del__(self):
        self.stop()


def main():
    global extract_to_base_folder, _gist_cfg, _gist_remote_ts

    # ① 确保 Gist 配置可用
    _gist_cfg = _ensure_gist_config()

    manager = FileManager()

    check_passwords()
    files_to_process = sys.argv[1:]

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
                    print_info(f"开始解压文件 {file_path} 喵❤")
                    base_folder = os.path.dirname(file_path)
                    if move_temp_folders_to_recycle_bin(base_folder):
                        print_info(
                            "检测到上一次非正常退出留下的临时文件夹喵！已经把它们全部移动到回收站了喵☆"
                        )
                    recursive_extract(
                        base_folder, file_path, global_last_success_password
                    )
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
    try:
        lock = FileLock(instance_lock)
        with lock.acquire(timeout=0):
            try:
                main()
                time.sleep(1)
            except Exception as e:
                error_end(e)
    except Timeout:
        if len(sys.argv) >= 2:
            # Try to send file paths to the existing instance
            send_file_to_main_instance(sys.argv[1:])
        print_info("检测到已经有一个实例在运行，已将任务添加到队列中喵！")
        pass
    except Exception:
        error_end()
