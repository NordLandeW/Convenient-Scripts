import subprocess
import os
import sys
import shutil
from rich.console import Console
from rich.progress import Progress
import rich.progress

console = Console()

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

def read_passwords():
    """从与脚本同一目录下的dict.txt中读取密码喵，如果文件不存在或为空则返回空列表"""
    passwords = []
    try:
        with open('dict.txt', 'r', encoding='utf-8') as file:
            passwords = file.read().splitlines()
    except FileNotFoundError:
        print_warning("未找到dict.txt文件喵，请确保文件在正确的位置！")
    return passwords if len(passwords)>0 else ["???"]

from tqdm import tqdm
import re
import time

def extract_with_7zip(file_path, extract_to, password=None):
    """使用7zip尝试解压文件到指定目录，可能需要密码，并实时显示美观的进度条喵"""
    command = ['7z', 'x', file_path, f'-o{extract_to}', '-y', '-bsp1']
    if password:
        command.extend(['-p' + password])

    # 启动7z进程
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    
    task = -1
    task_started = False
    last_percent = 0
    
    file_size = os.path.getsize(file_path)
    result = 1

    # 实时输出进度
    with Progress(
        rich.progress.SpinnerColumn(finished_text="✅"),
        rich.progress.TextColumn("[cyan][b]{task.fields[filename]}[/cyan][/b]"),
        rich.progress.BarColumn(),
        "[progress.percentage]{task.percentage:>3.1f}%",
        "•",
        rich.progress.FileSizeColumn(),
        "•",
        rich.progress.TransferSpeedColumn(),
        "•",
        rich.progress.TimeElapsedColumn(), "/",
        rich.progress.TimeRemainingColumn(),
        transient=True) as progress:
        for line in iter(process.stdout.readline, ''):
            line = line.strip()
            # print(line)
            if "- " in line:
                current_file = line.split("- ", 1)[1]
                if task < 0:
                    task = progress.add_task("Decompress...", total=file_size, filename=current_file, start=False)
                    # print(f"Add task {task}")
                    last_percent = 0
                elif task_started:
                    progress.update(task, filename=current_file)
            if "%" in line:
                match = re.search(r"(\d+)%", line)
                if match and task >= 0:
                    percent = int(match.group(0).replace("%", ""))
                    if not task_started:
                        if percent == 100:
                            continue
                        # print("Task start")
                        progress.live.transient = False
                        progress.start_task(task)
                        task_started = True
                    progress_increment = int((percent - last_percent) * file_size / 100) + 1
                    progress.update(task, advance=progress_increment, refresh=True)
                    last_percent = percent
            if "Everything is Ok" in line:
                if task >= 0:
                    progress.update(task, advance=int(file_size - last_percent * file_size / 100) + 1)
                    progress.refresh()

        stderr = process.communicate()[1]
        if stderr:
            if "wrong password" in stderr.lower():
                result = -1
            else:
                result = -2
    
    if result == -1:
        print_info(f"密码 {password} 尝试错误喵。")
    elif result == -2:
        print_warning(f"错误: {stderr}")
        print_info(f"{file_path}\n可能不是压缩文件喵。这种情况下上面的错误是正常现象喵。")
    else:
        print_success("解压完成：Everything is Ok")
    return result

def try_passwords(file_path, extract_to, passwords):
    """尝试一系列密码解压文件喵，如果没有有效密码则返回None"""
    for password in passwords:
        if extract_with_7zip(file_path, extract_to, password)>0:
            return password
    return None

def manual_password_entry(file_path, extract_to, level):
    """当字典中的密码都无效时，手动请求用户输入密码喵"""
    while True:
        console.print(f"[cyan][b]请输入第{level}层文件的解压密码喵：", end="")
        password = input()
        if extract_with_7zip(file_path, extract_to, password)>0:
            return password
        print_warning("密码错误，请重新输入喵！")

global_last_success_password = None

def recursive_extract(base_folder, file_path, last_success_password=None, level = 1):
    """递归解压文件，处理密码保护的压缩文件喵"""
    temp_folder = create_unique_directory(base_folder, "temp_extract")
    last_compressed_file_name = os.path.splitext(os.path.basename(file_path))[0]

    passwords = read_passwords()
    password = last_success_password if last_success_password is not None else passwords[0]
    first_try = extract_with_7zip(file_path, temp_folder, password)
    if first_try == -1:
        password = try_passwords(file_path, temp_folder, passwords) or manual_password_entry(file_path, temp_folder, level)
    elif first_try == -2:
        shutil.rmtree(temp_folder)
        return True
    
    global_last_success_password = password

    files = os.listdir(temp_folder)
    orig_temp_folder = temp_folder
    while len(files) == 1 and os.path.isdir(os.path.join(temp_folder, files[0])):
        deeper_folder = os.path.join(temp_folder, files[0])
        files = os.listdir(deeper_folder)
        temp_folder = deeper_folder

    finished = False
    if len(files) == 1:
        new_file_path = os.path.join(temp_folder, files[0])
        finished = recursive_extract(base_folder, new_file_path, password, level + 1)
        if not finished:
            os.remove(new_file_path)
    else:
        finished = True
    if finished:
        target_folder = create_unique_directory(base_folder, last_compressed_file_name)
        for f in os.listdir(orig_temp_folder):
            shutil.move(os.path.join(orig_temp_folder, f), target_folder)
        print_success(f"最终文件被移动到：{target_folder}")
    shutil.rmtree(orig_temp_folder)
    return False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        for i in range(1, len(sys.argv)):
            print_info(f"开始解压文件 {sys.argv[i]} 喵❤")
            base_folder = os.path.dirname(sys.argv[i])
            recursive_extract(base_folder, sys.argv[i], global_last_success_password)
        input("解压完成，按任意键退出程序喵...")
    else:
        print_warning("请拖拽一个文件到这个脚本上进行解压喵！")
        input("")