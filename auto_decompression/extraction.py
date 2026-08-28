import os
import re
import subprocess
import threading

import rich.progress
from rich.console import Console
from rich.progress import Progress

console = Console()


def print_info(message):
    console.out(message, style="blue")


def print_error(message):
    console.out(message, style="bold red")


def print_success(message):
    console.out(message, style="green")


def print_warning(message):
    console.out(message, style="bold yellow underline")


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
