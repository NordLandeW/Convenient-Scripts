#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
minimize_dict.py
----------------
本脚本用于处理当前目录下的 dict.txt 文件，功能包括：
  1. 删除所有重复的字符（保留第一次出现的顺序）。
  2. 去除所有空白字符（如空格、换行等），使文件内容最小化。
  3. 将处理后的结果覆盖写回 dict.txt 文件。

使用示例：
    python minimize_dict.py
"""

import os
import sys

def read_dict_file(filename):
    """读取文件内容"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except Exception as e:
        print(f"读取文件 {filename} 失败：{e}")
        sys.exit(1)

def minimize_content(content):
    """
    去除所有空白字符，然后删除重复字符，保留第一次出现的顺序。
    """
    # 去除所有空白字符（包括空格、换行、制表符等）
    stripped = "".join(ch for ch in content if not ch.isspace())
    seen = set()
    result = []
    for ch in stripped:
        if ch not in seen:
            seen.add(ch)
            result.append(ch)
    return "".join(result)

def write_dict_file(filename, content):
    """将内容写入文件"""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        print(f"写入文件 {filename} 失败：{e}")
        sys.exit(1)

def main():
    dict_filename = "dict.txt"
    if not os.path.exists(dict_filename):
        print(f"文件 {dict_filename} 不存在！")
        sys.exit(1)

    # 读取原始文件内容
    original_content = read_dict_file(dict_filename)
    print("原始文件内容长度：", len(original_content))

    # 处理内容：去除空白并删除重复字符
    minimized = minimize_content(original_content)
    print("最小化后内容长度：", len(minimized))

    # 写回文件
    write_dict_file(dict_filename, minimized)
    print(f"处理完成，已覆盖写回到 {dict_filename} 文件。")

if __name__ == "__main__":
    main()
