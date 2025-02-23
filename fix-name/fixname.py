#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文件名修复脚本
---------------
本脚本用于遍历指定目录及其子目录下的所有文件和目录，对因编码错误导致的“乱码文件名”进行预览或实际修复。

【新增功能】
1. 可通过参数 --dict 指定一个常用字词典文件（默认 dict.txt），
   文件中存放纯文本（无格式）的常用字符。在启发式评分时， 如果候选转换结果中出现字典内字符，则每个匹配加 3 分。
2. 脚本默认以预览模式运行。如果未同时指定 --current-enc 和 --actual-enc，
   则仅进行预览；只有当同时提供这两个参数时，才直接执行修复操作。
3. 在预览模式下，脚本遍历所有文件/目录，展示各候选转换结果并累加全局得分，
   最后输出累计得分最高的 10 个候选转换选项，并随机抽样展示每个候选的 3 个示例，
   同时在预览结束后询问是否使用全局最高评分的候选方案直接进行修复。
4. 脚本结束后等待5秒后再退出。

【启发式评分规则】
  - 每个 CJK 字符 +1 分  
  - 每个替换字符 “�” -10 分  
  - 如果候选转换结果中出现的每个常用字（字典内字符），额外加 3 分  

使用示例：
  1. 预览模式（默认模式，可指定字典文件）：  
         python fix_filename.py --dir "C:\待处理目录" --dict dict.txt
  2. 实际修复模式（必须同时指定当前错误编码和实际编码）：  
         python fix_filename.py --dir "C:\待处理目录" --current-enc gbk --actual-enc utf-8
"""

import os
import sys
import argparse
import random
import time
from colorama import init, Fore, Style

# 初始化 colorama（在 Windows 下处理 ANSI 颜色转码）
init(autoreset=True)

# 针对 CJK 常用的候选编码列表
CANDIDATE_ENCODINGS = ["gb18030", "gbk", "gb2312", "big5", "shift_jis", "euc-kr", "utf-8"]

def is_cjk(ch):
    """
    判断字符是否属于常用 CJK 区间（含中日韩常用字）
    """
    code = ord(ch)
    return ((0x4E00 <= code <= 0x9FFF) or    # 中日韩统一表意文字
            (0x3400 <= code <= 0x4DBF) or    # 中日韩统一表意文字扩展 A
            (0x3040 <= code <= 0x30FF) or    # 日文平假名、片假名
            (0xAC00 <= code <= 0xD7AF))      # 韩文音节

def get_fixed_name(original, current_enc, actual_enc):
    """
    对原始名称做转换：
      1. 先用当前编码将字符串编码为 bytes；
      2. 再用实际编码解码为字符串；
    errors='replace' 保证出错时用替换符“�”填充。
    """
    try:
        fixed = original.encode(current_enc, errors='replace').decode(actual_enc, errors='replace')
        return fixed
    except Exception as e:
        return None

def score_conversion(original, fixed, common_chars=None):
    """
    对转换结果打分：
      - 每个 CJK 字符 +1 分
      - 每个替换字符 “�” -10 分
      - 对于候选转换结果中出现的每个常用字（字典内字符），额外加 3 分
    """
    if fixed is None:
        return -1000
    num_replace = fixed.count("�")
    num_cjk = sum(1 for ch in fixed if is_cjk(ch))
    bonus = 0
    if common_chars:
        bonus = sum(3 for ch in fixed if ch in common_chars)
    score = num_cjk + bonus - num_replace * 10
    if fixed == original:
        score -= 10
    return score

def process_item(name, global_candidate_scores, global_candidate_examples, common_chars):
    """
    对单个文件或目录名称，尝试所有候选转换，并更新全局统计字典和示例字典。

    返回：
       candidates: list of (score, cur_enc, act_enc, fixed) 按分数降序排序，
                   用于本文件/目录的预览展示。
    """
    candidates = []
    for cur_enc in CANDIDATE_ENCODINGS:
        for act_enc in CANDIDATE_ENCODINGS:
            fixed = get_fixed_name(name, cur_enc, act_enc)
            if fixed is None:
                continue
            score = score_conversion(name, fixed, common_chars)
            candidates.append((score, cur_enc, act_enc, fixed))
            key = (cur_enc, act_enc)
            global_candidate_scores[key] = global_candidate_scores.get(key, 0) + score
            if key not in global_candidate_examples:
                global_candidate_examples[key] = []
            global_candidate_examples[key].append((name, fixed))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates

def preview_mode(directory, dict_file):
    """
    预览模式：
      - 遍历目录及其子目录，对每个文件和目录名称展示所有候选转换结果。
      - 如果指定了常用字词典文件，则加载后在评分时为匹配字典内字符加分。
      - 遍历过程中，对所有候选转换的得分进行全局累加，
        最后输出累计得分最高的 10 个候选转换选项，并随机抽样展示每个候选转换的 3 个示例。
      - 最后询问用户是否使用全局最高评分的候选转换直接进行修复，若确认则自动调用修复流程。
    """
    # 加载常用字词典（如果文件存在）
    common_chars = None
    if dict_file and os.path.exists(dict_file):
        try:
            with open(dict_file, 'r', encoding='utf-8') as f:
                text = f.read()
                # 去除空白字符后得到常用字符集合
                common_chars = set(ch for ch in text if not ch.isspace())
            print(f"加载常用字词典：{dict_file}，共 {len(common_chars)} 个字符")
        except Exception as e:
            print(f"{Fore.RED}加载字典文件 {dict_file} 失败，忽略字典加分项，错误：{e}{Style.RESET_ALL}")
            common_chars = None
    else:
        if dict_file:
            print(f"{Fore.YELLOW}字典文件 {dict_file} 不存在，将忽略字典加分项。{Style.RESET_ALL}")
        common_chars = None

    global_candidate_scores = {}     # 键：(cur_enc, act_enc)；值：累计得分
    global_candidate_examples = {}     # 键：(cur_enc, act_enc)；值：[(原名称, 转换后名称), ...]

    # 遍历所有文件和目录，检查路径本身
    for root, dirs, files in os.walk(directory):
        # 检查当前路径（文件夹路径）本身
        print(f"\n【路径】：{root}")
        candidates = process_item(root, global_candidate_scores, global_candidate_examples, common_chars)
        for score, cur_enc, act_enc, fixed in candidates:
            if score >= 5:
                color = Fore.GREEN
            elif score >= 0:
                color = Fore.YELLOW
            else:
                color = Fore.RED
            print(f"  [{cur_enc:>9} -> {act_enc:<9}] Score: {score:>3} : {color}{fixed}{Style.RESET_ALL}")
        
        # 处理文件
        for name in files:
            full_path = os.path.join(root, name)
            print(f"\n【文件】：{full_path}")
            candidates = process_item(name, global_candidate_scores, global_candidate_examples, common_chars)
            for score, cur_enc, act_enc, fixed in candidates:
                if score >= 5:
                    color = Fore.GREEN
                elif score >= 0:
                    color = Fore.YELLOW
                else:
                    color = Fore.RED
                print(f"  [{cur_enc:>9} -> {act_enc:<9}] Score: {score:>3} : {color}{fixed}{Style.RESET_ALL}")
        
        # 处理目录
        for name in dirs:
            full_path = os.path.join(root, name)
            print(f"\n【目录】：{full_path}")
            candidates = process_item(name, global_candidate_scores, global_candidate_examples, common_chars)
            for score, cur_enc, act_enc, fixed in candidates:
                if score >= 5:
                    color = Fore.GREEN
                elif score >= 0:
                    color = Fore.YELLOW
                else:
                    color = Fore.RED
                print(f"  [{cur_enc:>9} -> {act_enc:<9}] Score: {score:>3} : {color}{fixed}{Style.RESET_ALL}")

    # 全局统计输出：选出累计得分最高的 10 个候选转换选项，并随机抽样展示 3 个示例
    print("\n============================")
    print("【总体候选转换统计】")
    sorted_global = sorted(global_candidate_scores.items(), key=lambda x: x[1], reverse=True)
    top_10 = sorted_global[:10]
    for (cur_enc, act_enc), total_score in top_10:
        print(f"\n  [{cur_enc:>9} -> {act_enc:<9}] Aggregated Score: {total_score}")
        examples = global_candidate_examples.get((cur_enc, act_enc), [])
        if examples:
            sample_size = min(3, len(examples))
            sampled = random.sample(examples, sample_size)
            for orig, fixed in sampled:
                print(f"     示例: 原: {orig}  =>  新: {fixed}")
        else:
            print("     无示例数据。")
    print("============================\n")

    # 询问用户是否使用全局最高评分的候选转换方案直接进行修复
    if sorted_global:
        best_key, best_score = sorted_global[0]
        best_cur_enc, best_act_enc = best_key
        choice = input("是否使用全局最高评分候选项 [%s -> %s] 直接进行修复? (y/n): " % (best_cur_enc, best_act_enc))
        if choice.strip().lower() == 'y':
            print("开始使用候选转换方案 [%s -> %s] 进行文件名修复..." % (best_cur_enc, best_act_enc))
            fix_mode(directory, best_cur_enc, best_act_enc)
            print("修复完成。")
        else:
            print("未进行修复操作。")

def fix_mode(directory, current_enc, actual_enc):
    """
    实际修复模式：
      - 遍历目录及其子目录，对文件和目录名称使用指定的编码进行转换重命名。
      - 遍历采用自底向上方式，避免先重命名父目录而影响后续处理。
      - 如果转换后名称与原名称相同或转换失败，则跳过重命名；
        如果目标名称已存在，则提示并跳过。
    """
    for root, dirs, files in os.walk(directory, topdown=False):
        # 检查并修复路径本身
        new_root = get_fixed_name(root, current_enc, actual_enc)
        if new_root and new_root != root:
            os.rename(root, new_root)
            print(f"目录重命名：\n  {root}\n  -->\n  {new_root}")
        
        for name in files:
            new_name = get_fixed_name(name, current_enc, actual_enc)
            if new_name is None or new_name == name:
                continue
            old_path = os.path.join(root, name)
            new_path = os.path.join(root, new_name)
            if os.path.exists(new_path):
                print(f"{Fore.RED}跳过文件：{old_path} —— 目标名称 {new_path} 已存在！{Style.RESET_ALL}")
            else:
                try:
                    os.rename(old_path, new_path)
                    print(f"文件重命名：\n  {old_path}\n  -->\n  {new_path}")
                except Exception as e:
                    print(f"{Fore.RED}重命名失败：{old_path} -> {new_path}，错误：{e}{Style.RESET_ALL}")
        
        for name in dirs:
            new_name = get_fixed_name(name, current_enc, actual_enc)
            if new_name is None or new_name == name:
                continue
            old_path = os.path.join(root, name)
            new_path = os.path.join(root, new_name)
            if os.path.exists(new_path):
                print(f"{Fore.RED}跳过目录：{old_path} —— 目标名称 {new_path} 已存在！{Style.RESET_ALL}")
            else:
                try:
                    os.rename(old_path, new_path)
                    print(f"目录重命名：\n  {old_path}\n  -->\n  {new_path}")
                except Exception as e:
                    print(f"{Fore.RED}重命名失败：{old_path} -> {new_path}，错误：{e}{Style.RESET_ALL}")

def main():
    parser = argparse.ArgumentParser(
        description="修复文件名乱码的脚本（主要针对 CJK 文字），支持常用字词典加分，并默认以预览模式运行。"
    )
    parser.add_argument("--dir", default=".", help="指定遍历的目录（默认当前目录）")
    parser.add_argument("--current-enc", help="当前文件名错误解码时所用的编码（如 gbk）")
    parser.add_argument("--actual-enc", help="原始正确的文件名编码（如 utf-8）")
    parser.add_argument("--dict", dest="dict_file", default="dict.txt",
                        help="指定常用字词典文件路径，默认为 dict.txt")
    args = parser.parse_args()
    directory = args.dir

    # 如果同时指定了 current_enc 与 actual_enc，则进入修复模式；否则默认预览模式
    if args.current_enc and args.actual_enc:
        fix_mode(directory, args.current_enc, args.actual_enc)
    else:
        preview_mode(directory, args.dict_file)

    # 脚本结束后等待 5 秒再退出
    print("\n脚本将在 5 秒后退出...")
    time.sleep(5)

if __name__ == "__main__":
    main()
