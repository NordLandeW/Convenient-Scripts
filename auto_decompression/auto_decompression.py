import subprocess
import os
import sys
import shutil

def print_info(message):
    """用蓝色打印普通信息喵"""
    print(f"\033[34m{message}\033[0m")

def print_error(message):
    """用红色加粗打印错误信息喵"""
    print(f"\033[1;31m{message}\033[0m")

def print_success(message):
    """用绿色打印成功信息喵"""
    print(f"\033[32m{message}\033[0m")

def print_warning(message):
    """用黄色打印警告信息喵"""
    print(f"\033[33m{message}\033[0m")

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
    return passwords

from tqdm import tqdm
import re

def extract_with_7zip(file_path, extract_to, password=None):
    """使用7zip尝试解压文件到指定目录，可能需要密码，并实时显示美观的进度条喵"""
    command = ['7z', 'x', file_path, f'-o{extract_to}', '-y', '-bsp1']
    if password:
        command.extend(['-p' + password])

    # 启动7z进程
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    
    progress_bar = None
    last_percent = 0
    
    file_size = os.path.getsize(file_path) / (1024*1024)

    # 实时输出进度
    for line in iter(process.stdout.readline, ''):
        line = line.strip()
        if "- " in line:
            current_file = line.split("- ", 1)[1]
            if progress_bar is None:
                bar_format = "{desc}: {percentage}%|{bar}| {n:.2f}/{total:.2f} [{elapsed}<{remaining}, {rate_fmt}{postfix}]"
                progress_bar = tqdm(desc = current_file, total=file_size, unit="MB", bar_format=bar_format)
                last_percent = 0
            else:
                progress_bar.set_description(current_file)
        if "%" in line:
            match = re.search(r"(\d+)%", line)
            if match and progress_bar:
                percent = int(match.group(0).replace("%", ""))
                progress_increment = (percent - last_percent) * file_size / 100
                progress_bar.update(progress_increment)
                last_percent = percent
        if "Everything is Ok" in line:
            if progress_bar:
                progress_bar.close()
            print_success("解压完成：Everything is Ok")

    stderr = process.communicate()[1]
    if stderr:
        print_error(f"错误: {stderr}")
    if "wrong password" in stderr.lower():
        return False
    return True

def try_passwords(file_path, extract_to, passwords):
    """尝试一系列密码解压文件喵，如果没有有效密码则返回None"""
    for password in passwords:
        if extract_with_7zip(file_path, extract_to, password):
            return password
    return None

def manual_password_entry(file_path, extract_to, level):
    """当字典中的密码都无效时，手动请求用户输入密码喵"""
    while True:
        password = input(f"请输入第 {level} 层文件的解压密码喵：")
        if extract_with_7zip(file_path, extract_to, password):
            return password
        print("密码错误，请重新输入喵！")

def recursive_extract(base_folder, file_path, last_success_password=None, level = 1):
    """递归解压文件，处理密码保护的压缩文件喵"""
    temp_folder = create_unique_directory(base_folder, "temp_extract")
    last_compressed_file_name = os.path.splitext(os.path.basename(file_path))[0]

    passwords = read_passwords()
    password = last_success_password if last_success_password in passwords else None
    if password is None or not extract_with_7zip(file_path, temp_folder, password):
        password = try_passwords(file_path, temp_folder, passwords) or manual_password_entry(file_path, temp_folder, level)

    files = os.listdir(temp_folder)
    orig_temp_folder = temp_folder
    while len(files) == 1 and os.path.isdir(os.path.join(temp_folder, files[0])):
        deeper_folder = os.path.join(temp_folder, files[0])
        files = os.listdir(deeper_folder)
        temp_folder = deeper_folder

    if len(files) == 1 and not os.path.isdir(os.path.join(temp_folder, files[0])):
        new_file_path = os.path.join(temp_folder, files[0])
        recursive_extract(base_folder, new_file_path, password, level + 1)
        os.remove(new_file_path)
    else:
        target_folder = create_unique_directory(base_folder, last_compressed_file_name)
        for f in files:
            shutil.move(os.path.join(temp_folder, f), target_folder)
        print_success(f"最终文件被移动到：{target_folder}")
    shutil.rmtree(orig_temp_folder)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        base_folder = os.path.dirname(sys.argv[1])
        recursive_extract(base_folder, sys.argv[1])
        input("解压完成，按任意键退出程序喵...")
    else:
        print_warning("请拖拽一个文件到这个脚本上进行解压喵！")
        input("")