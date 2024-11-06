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

__version__ = "1.0.0"
console = Console()
extract_to_base_folder = False
pwdOldFilename = "dict.txt"
pwdFilename = "dict.json"
pwdDictionary = {}
RECOVER_SUFFIX = ".AutoDecRecovered"

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
    temp_folders = [item for item in items if os.path.isdir(os.path.join(current_directory, item)) and item.startswith('temp_extract')]
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
        with open(pwdPath, 'r', encoding='utf-8') as file:
            passwords = file.read().splitlines()
    except Exception as e:
        print_warning(f"读取旧密码文件错误喵！错误信息：{e}")

    return passwords if len(passwords)>0 else ["???"]

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
        with open(pwdPath, 'r', encoding='utf-8') as file:
            pwdDictionary = json.load(file)
    except Exception as e:
        print_warning(f"读取文件错误喵！错误信息：{e}")

def save_passwords():
    # print(str(pwdDictionary))
    pwdPath = os.path.join(sys.path[0], pwdFilename)
    try:
        with open(pwdPath, 'w', encoding='utf-8') as file:
            json.dump(pwdDictionary, file, ensure_ascii=False, indent=4)
        # print(f"密码已成功保存到 {pwdPath} 喵～")
    except Exception as e:
        print_warning(f"保存密码时出错喵！请检查文件权限或路径。错误信息：{e}")

def check_passwords():
    global pwdDictionary
    pwdPath = os.path.join(sys.path[0], pwdFilename)
    pwdOldPath = os.path.join(sys.path[0], pwdOldFilename)
    if not os.path.exists(pwdPath):
        # Check if a old version file exists.
        if os.path.exists(pwdOldPath):
            convert_old_pwd_to_new_pwd(read_passwords_old())
        else:
            pwdDictionary = {}
    else:
        read_passwords()

def add_password(pwd, count = 1):
    if pwd == None:
        return
    if pwd in pwdDictionary:
        pwdDictionary[pwd] += count;
    else:
        pwdDictionary[pwd] = count;

import re
import threading

def extract_with_7zip(file_path, extract_to, password:str=None):
    """使用7zip尝试解压文件到指定目录，可能需要密码，并实时显示美观的进度条喵"""
    command = ['7z', 'x', file_path, f'-o{extract_to}', '-y', '-bsp1', '-bb3']
    if password:
        command.extend(['-p' + password])

    # 启动7z进程
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    
    task = -1
    last_percent = 0
    file_size = os.path.getsize(file_path)
    result = 1
    err_log = ""

    # 定义处理 stdout 的函数
    def handle_stdout():
        nonlocal last_percent, task, result
        for line in iter(process.stdout.readline, ''):
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
                    progress_increment = int((percent - last_percent) * file_size / 100) + 1
                    progress.update(task, advance=progress_increment, refresh=True)
                    last_percent = percent
            if "Everything is Ok" in line:
                progress.update(task, advance=int(file_size - last_percent * file_size / 100) + 1)
                progress.refresh()

    # 定义处理 stderr 的函数
    def handle_stderr():
        nonlocal result
        nonlocal err_log
        for err_line in iter(process.stderr.readline, ''):
            err_line = err_line.strip()
            err_log += err_line + "\n"
            # print_error(f"\n{err_line}\n")
            if err_line:
                process.terminate()
                # 检查错误信息
                if "wrong password" in err_line.lower():
                    result = -1
                    break
                elif "cannot open the file as archive" in err_line.lower():
                    result = -2
                    break
                else:
                    result = -3

    # 实时输出进度
    with Progress(
        rich.progress.SpinnerColumn(finished_text="✅"),
        rich.progress.TextColumn("[cyan][b]{task.fields[filename]}[/cyan][/b]",
                                table_column=rich.progress.Column(max_width = 75)),
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

def try_passwords(file_path, extract_to, passwords, last_tried_password):
    """尝试一系列密码解压文件喵，如果没有有效密码则返回None"""
    for password in passwords:
        password = password[0]
        if last_tried_password == password:
            continue
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

def try_remove_directory(dir):
    try:
        shutil.rmtree(dir)
    except:
        pass

def recursive_extract(base_folder, file_path, last_success_password=None, level = 1):
    """递归解压文件，处理密码保护的压缩文件喵"""
    temp_folder = create_unique_directory(base_folder, "temp_extract")
    last_compressed_file_name = os.path.splitext(os.path.basename(file_path))[0]

    passwords = sorted(pwdDictionary.items(), key=lambda item: item[1], reverse=True)
    password = last_success_password if last_success_password is not None else passwords[0][0]
    
    while True:
        tryResult = extract_with_7zip(file_path, temp_folder, password)
        if tryResult == -1:
            password = try_passwords(file_path, temp_folder, passwords, password) or manual_password_entry(file_path, temp_folder, level)
            break
        elif tryResult == -2:
            if hiddenZip.has_embedded_signature(file_path, hiddenZip.ZIP_SIGNATURE):
                print_info("发现文件嵌入了隐藏Zip喵，准备处理喵！")
                hiddenZip.extract_embedded_file(file_path, file_path+RECOVER_SUFFIX, hiddenZip.ZIP_SIGNATURE)
                file_path = file_path+RECOVER_SUFFIX
            elif hiddenZip.has_embedded_signature(file_path, hiddenZip.RAR_SIGNATURE):
                print_info("发现文件嵌入了隐藏RAR喵，准备处理喵！")
                hiddenZip.extract_embedded_file(file_path, file_path+RECOVER_SUFFIX, hiddenZip.RAR_SIGNATURE)
                file_path = file_path+RECOVER_SUFFIX
            else:
                try_remove_directory(temp_folder)
                return True
        else:
            break
    
    global_last_success_password = password
    add_password(password)

    files = os.listdir(temp_folder)
    orig_temp_folder = temp_folder
    while len(files) == 1 and os.path.isdir(os.path.join(temp_folder, files[0])):
        deeper_folder = os.path.join(temp_folder, files[0])
        files = os.listdir(deeper_folder)
        temp_folder = deeper_folder
        last_compressed_file_name = os.path.basename(temp_folder)

    finished = False
    if len(files) == 1:
        new_file_path = os.path.join(temp_folder, files[0])
        finished = recursive_extract(base_folder, new_file_path, password, level + 1)
        if not finished:
            os.remove(new_file_path)
    else:
        finished = True
    if finished:
        if extract_to_base_folder:
            target_folder = base_folder
        else:
            target_folder = create_unique_directory(base_folder, last_compressed_file_name)
        for f in files:
            shutil.move(os.path.join(temp_folder, f), target_folder)
        print_success(f"最终文件被移动到：{target_folder}")
    try_remove_directory(orig_temp_folder)
    return False

import time

def main():
    check_passwords()
    if len(sys.argv) > 1:
        console.print("[cyan][b]要为每个压缩包单独建立一个文件夹吗？[Y/n]：", end="")
        question = input()
        if question.lower() == "n":
            print_info("将所有压缩包内的文件都解压到当前文件夹下喵❤")
            extract_to_base_folder=True
        for i in range(1, len(sys.argv)):
            print_info(f"开始解压文件 {sys.argv[i]} 喵❤")
            base_folder = os.path.dirname(sys.argv[i])
            if move_temp_folders_to_recycle_bin(base_folder):
                print_info("检测到上一次非正常退出留下的临时文件夹喵！已经把它们全部移动到回收站了喵☆")
            recursive_extract(base_folder, sys.argv[i], global_last_success_password)
            remove_autodec_files(base_folder)
        save_passwords()
        print_info("解压完成，退出程序喵...")
        time.sleep(1)
    else:
        print_warning("请拖拽一个文件到这个脚本上进行解压喵！")
        input("")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print_error(f"程序出现错误喵>.< 非常抱歉喵，下面是错误信息喵！\n{traceback.format_exc()}")
        input()
        