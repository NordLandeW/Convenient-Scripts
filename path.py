#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows PATH 追加工具
用法:
  python path.py [--system] [--head] <PATH>
  直接调用: path.py [--system] [--head] <PATH>
说明:
  默认追加到“用户”环境变量 Path；使用 --system 追加到“系统”环境变量 Path（将自动提权）。
  使用 --head 可将路径添加到 PATH 的最前面；默认追加到末尾。
"""

import os
import sys
import argparse
import platform
import ctypes
import subprocess
import traceback
from typing import Tuple, List
import atexit
import time

try:
    import winreg as reg  # type: ignore
except Exception:
    reg = None  # type: ignore

USER_ROOT = None
USER_SUBKEY = r"Environment"
SYSTEM_ROOT = None
SYSTEM_SUBKEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002

def _wait_on_exit() -> None:
    try:
        time.sleep(3)
    except Exception:
        pass

atexit.register(_wait_on_exit)


def _ensure_windows() -> None:
    if platform.system() != "Windows":
        print("错误: 本工具仅支持 Windows。", file=sys.stderr)
        sys.exit(1)
    if reg is None:
        print("错误: 无法导入 winreg。", file=sys.stderr)
        sys.exit(1)


def _init_registry_roots() -> None:
    global USER_ROOT, SYSTEM_ROOT
    USER_ROOT = reg.HKEY_CURRENT_USER
    SYSTEM_ROOT = reg.HKEY_LOCAL_MACHINE


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate_for_system_if_needed(argv: List[str]) -> None:
    """
    如果需要系统级修改但当前进程无管理员权限，则触发 UAC 提权并退出当前进程。
    """
    if not is_admin():
        # 使用 ShellExecuteW 提权运行当前脚本
        try:
            params_list = [os.path.abspath(__file__)] + argv[1:]
            param_str = subprocess.list2cmdline(params_list)
            hinstance = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, param_str, os.getcwd(), 1
            )
            if hinstance <= 32:
                print(f"错误: 提权失败 (ShellExecuteW 返回 {hinstance}).", file=sys.stderr)
                sys.exit(2)
            sys.exit(0)
        except Exception as e:
            print("错误: 自动提权失败。", file=sys.stderr)
            print(str(e), file=sys.stderr)
            sys.exit(2)


def _registry_access_flags(for_write: bool) -> int:
    access = reg.KEY_READ
    if for_write:
        access |= reg.KEY_SET_VALUE
    try:
        access |= reg.KEY_WOW64_64KEY
    except Exception:
        pass
    return access


def read_path(scope_system: bool) -> Tuple[str, int]:
    root = SYSTEM_ROOT if scope_system else USER_ROOT
    subkey = SYSTEM_SUBKEY if scope_system else USER_SUBKEY
    with reg.OpenKey(root, subkey, 0, _registry_access_flags(for_write=False)) as key:  # type: ignore[arg-type]
        try:
            value, regtype = reg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value, regtype = "", reg.REG_EXPAND_SZ
    if not isinstance(value, str):
        value = ""
    return value, regtype


def write_path(scope_system: bool, new_value: str, regtype: int) -> None:
    root = SYSTEM_ROOT if scope_system else USER_ROOT
    subkey = SYSTEM_SUBKEY if scope_system else USER_SUBKEY
    with reg.OpenKey(root, subkey, 0, _registry_access_flags(for_write=True)) as key:  # type: ignore[arg-type]
        reg.SetValueEx(key, "Path", 0, regtype, new_value)


def broadcast_env_change() -> None:
    try:
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, None
        )
    except Exception:
        pass


def _split_path(value: str) -> List[str]:
    parts = [p for p in value.split(";") if p is not None]
    cleaned: List[str] = []
    for p in parts:
        s = p.strip()
        if s:
            cleaned.append(s)
    return cleaned


def _canonical(entry: str) -> str:
    s = (entry or "").strip().strip('"').replace("/", "\\")
    expanded = os.path.expandvars(os.path.expanduser(s))
    try:
        expanded = os.path.abspath(expanded)
    except Exception:
        pass
    expanded = os.path.normpath(expanded)
    return expanded.lower()


def _normalize_to_write(p: str) -> str:
    s = os.path.expandvars(os.path.expanduser(p.strip().strip('"')))
    s = s.replace("/", "\\")
    s = os.path.abspath(s)
    s = os.path.normpath(s)
    return s


def add_to_path(target: str, to_head: bool, scope_system: bool) -> str:
    current_value, regtype = read_path(scope_system)
    entries = _split_path(current_value)
    target_norm = _normalize_to_write(target)
    target_key = _canonical(target_norm)

    existing_keys = {_canonical(e) for e in entries}
    if target_key in existing_keys:
        return current_value

    if to_head:
        new_entries = [target_norm] + entries
    else:
        new_entries = entries + [target_norm]

    new_value = ";".join(new_entries)
    write_path(scope_system, new_value, regtype)
    return new_value


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将指定目录追加到 Windows 用户环境变量 PATH。"
    )
    parser.add_argument("path", help="要追加的目录路径")
    parser.add_argument(
        "-s", "--system", action="store_true", help="追加到系统环境变量 PATH（需要管理员权限）"
    )
    parser.add_argument(
        "-H", "--head", action="store_true", help="将路径追加到 PATH 的最前面"
    )
    return parser.parse_args(argv[1:])


def main(argv: List[str]) -> int:
    _ensure_windows()
    _init_registry_roots()
    args = parse_args(argv)

    if args.system and not is_admin():
        elevate_for_system_if_needed(argv)
        return 0

    try:
        before, _ = read_path(args.system)
        after = add_to_path(args.path, args.head, args.system)
        if after == before:
            scope = "系统" if args.system else "用户"
            print(f"路径已存在于 {scope} PATH 中：{_normalize_to_write(args.path)}")
            return 0

        if args.head:
            os.environ["PATH"] = f"{_normalize_to_write(args.path)};{os.environ.get('PATH', '')}"
        else:
            os.environ["PATH"] = f"{os.environ.get('PATH', '')};{_normalize_to_write(args.path)}"

        broadcast_env_change()

        scope = "系统" if args.system else "用户"
        pos = "最前面" if args.head else "末尾"
        print(f"已将路径添加到 {scope} PATH 的{pos}：{_normalize_to_write(args.path)}")
        print("注意：已打开的终端/应用需要重新启动才能读取到最新环境变量。")
        return 0
    except Exception as e:
        print("发生错误：", file=sys.stderr)
        print(str(e), file=sys.stderr)
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))