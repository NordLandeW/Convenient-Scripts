import os
import subprocess
import sys
import ctypes
import tkinter as tk
from tkinter import filedialog

def is_admin():
    """检查当前用户是否是管理员喵"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_as_admin():
    """用管理员权限重新启动自己，喵呜~"""
    script_path = sys.argv[0]
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{script_path}"', None, 1)

def main():
    """主程序开始啦，喵~"""
    
    print("-------------------------------------------------")
    print("防火墙规则设置小助手已启动，随时为主人服务喵！")
    print("-------------------------------------------------")
    task_name_prompt = "主人，请给这次的任务起个名字吧（比如 Sogou, 360...），然后按Enter喵：\n> "
    task_name_input = input(task_name_prompt)
    task_name = task_name_input.strip() if task_name_input.strip() else "MyBlockedApp"
    print(f"好的喵！这次的任务代号是：【{task_name}】\n")

    print("接下来，请主人在弹出的窗口里，选择要禁止联网的程序所在的文件夹喵...")
    try:
        root = tk.Tk()
        root.withdraw()
        folder_path = filedialog.askdirectory(title=f"请选择要为【{task_name}】设置规则的文件夹喵~")
    except Exception as e:
        print(f"呜...创建文件夹选择窗口失败了喵...错误信息: {e}")
        folder_path = None

    if not folder_path:
        print("\n主人没有选择文件夹，任务取消了喵。有需要随时再叫我！")
        return

    # 【重要修复 ①】: 将路径标准化，统一使用反斜杠 '\'
    folder_path = os.path.normpath(folder_path)

    if not os.path.isdir(folder_path):
        print(f"咦？这个文件夹好像不存在哦 -> {folder_path}，请主人检查一下喵。")
        return

    print(f"\n好嘞！本喵将要'重点关照'这个文件夹：\n{folder_path}\n")
    print("正在努力工作中，请稍等喵...")

    executables = []
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if file.lower().endswith('.exe'):
                executables.append(os.path.join(root, file))

    if not executables:
        print("奇怪喵... 在这个文件夹里一个.exe文件都找不到呢。")
        return

    success_count = 0
    fail_count = 0
    for exe_path in executables:
        exe_name = os.path.basename(exe_path)
        print(f"--- 正在处理：{exe_name} ---")
        
        try:
            # 【重要修复 ②】: 为所有 subprocess 调用添加 encoding 和 errors 参数
            # 【重要修复 ③】: 将创建规则的操作包裹在 try...except 中，增强鲁棒性

            # --- 处理出站规则 ---
            rule_name_out = f'{task_name}Block_Outbound_{exe_name}'
            delete_cmd_out = f'netsh advfirewall firewall delete rule name="{rule_name_out}"'
            result_del_out = subprocess.run(delete_cmd_out, shell=True, capture_output=True, text=True, encoding='gbk', errors='ignore')
            if "确定" in result_del_out.stdout or "Ok." in result_del_out.stdout: # netsh 在不同系统语言下返回不同
                print("  (>^ω^<) 嘿嘿，找到了一个旧的出站规则，已经把它清理掉了喵！")
            
            add_cmd_out = f'netsh advfirewall firewall add rule name="{rule_name_out}" dir=out action=block program="{exe_path}"'
            subprocess.run(add_cmd_out, shell=True, check=True, capture_output=True, text=True, encoding='gbk', errors='ignore')
            print(f"  ✅ 已创建新的出站规则喵：{rule_name_out}")

            # --- 处理入站规则 ---
            rule_name_in = f'{task_name}Block_Inbound_{exe_name}'
            delete_cmd_in = f'netsh advfirewall firewall delete rule name="{rule_name_in}"'
            result_del_in = subprocess.run(delete_cmd_in, shell=True, capture_output=True, text=True, encoding='gbk', errors='ignore')
            if "确定" in result_del_in.stdout or "Ok." in result_del_in.stdout:
                print("  (>^ω^<) 嘿嘿，找到了一个旧的入站规则，也把它清理掉了喵！")

            add_cmd_in = f'netsh advfirewall firewall add rule name="{rule_name_in}" dir=in action=block program="{exe_path}"'
            subprocess.run(add_cmd_in, shell=True, check=True, capture_output=True, text=True, encoding='gbk', errors='ignore')
            print(f"  ✅ 已创建新的入站规则喵：{rule_name_in}\n")
            success_count += 1

        except subprocess.CalledProcessError as e:
            # 如果创建规则失败，打印详细错误后继续
            print(f"  ❌ 呜喵！为 {exe_name} 创建规则时失败了！")
            # 打印 netsh 命令返回的原始错误信息
            error_message = e.stdout or e.stderr
            print(f"  来自系统的错误报告：{error_message.strip()}\n")
            fail_count += 1
        except Exception as e:
            # 捕获其他意外错误
            print(f"  ❌ 糟糕！发生了意想不到的错误：{e}\n")
            fail_count += 1


    print("=================================================")
    print("报告主人！所有任务都完成了喵！ (ฅ'ω'ฅ)")
    print(f"成功处理了 {success_count} 个程序，有 {fail_count} 个失败了。")
    if fail_count == 0:
        print("完美！小程序们现在都不能上网捣乱啦！")
    else:
        print("有几个程序处理失败了，请主人向上翻看日志检查一下原因喵。")
    print("=================================================")


if __name__ == "__main__":
    if is_admin():
        main() # 不再在这里捕获异常，交给内部处理
    else:
        print("需要管理员权限才能设置防火墙哦，正在向系统申请变身超级管理员喵...")
        run_as_admin()
        sys.exit(0)
    
    input("\n请按 Enter 键让本喵休息去吧...")

