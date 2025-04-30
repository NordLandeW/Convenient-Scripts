#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文件名修复脚本
---------------
变更：
1. 预览结束时可从评分前十的方案中选择任意方案进行修复
2. 综合信息输出包含字典使用情况与修复数量
3. 日志写入脚本目录下 fix_filename.log
4. 预览时若转换前后名称一致则不输出
"""
import os
import sys
import argparse
import random
import time
from loguru import logger
from colorama import init, Fore, Style

init(autoreset=True)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
logger.add(os.path.join(SCRIPT_DIR, "fix_filename.log"), rotation="10 MB", encoding="utf-8", enqueue=True)

CANDIDATE_ENCODINGS = [
    "gb18030",
    "gbk",
    "gb2312",
    "big5",
    "shift_jis",
    "euc-kr",
    "utf-8",
]


def is_cjk(ch):
    code = ord(ch)
    return (
        (0x4E00 <= code <= 0x9FFF)
        or (0x3400 <= code <= 0x4DBF)
        or (0x3040 <= code <= 0x30FF)
        or (0xAC00 <= code <= 0xD7AF)
    )


def get_fixed_name(original, current_enc, actual_enc):
    try:
        return original.encode(current_enc, errors="replace").decode(actual_enc, errors="replace")
    except Exception:
        return None


def score_conversion(original, fixed, common_chars=None):
    if fixed is None:
        return -1000
    num_replace = fixed.count("�")
    bonus = sum(10 for ch in fixed if common_chars and ch in common_chars)
    return bonus - num_replace * 10


def process_item(name, global_scores, global_examples, common_chars):
    candidates = []
    for cur_enc in CANDIDATE_ENCODINGS:
        for act_enc in CANDIDATE_ENCODINGS:
            fixed = get_fixed_name(name, cur_enc, act_enc)
            if fixed is None:
                continue
            score = score_conversion(name, fixed, common_chars)
            candidates.append((score, cur_enc, act_enc, fixed))
            key = (cur_enc, act_enc)
            global_scores[key] = global_scores.get(key, 0) + score
            global_examples.setdefault(key, []).append((name, fixed))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates


def preview_mode(directory, dict_file):
    common_chars = None
    dict_path = os.path.join(SCRIPT_DIR, dict_file) if not os.path.isabs(dict_file) else dict_file
    if dict_file and os.path.exists(dict_path):
        try:
            with open(dict_path, "r", encoding="utf-8") as f:
                common_chars = set(ch for ch in f.read() if not ch.isspace())
            print(f"加载常用字词典：{dict_path}，{len(common_chars)} 字符")
            logger.info(f"Loaded dict {dict_path} with {len(common_chars)} chars")
        except Exception as e:
            print(f"{Fore.RED}加载字典失败：{e}{Style.RESET_ALL}")
            logger.error(f"Dict load error: {e}")
    else:
        if dict_file:
            print(f"{Fore.YELLOW}字典文件 {dict_path} 不存在{Style.RESET_ALL}")
            logger.warning(f"Dict {dict_path} not found")

    global_scores, global_examples = {}, {}

    for root, dirs, files in os.walk(directory):
        print(f"\n【路径】：{root}")
        for score, cur_enc, act_enc, fixed in process_item(root, global_scores, global_examples, common_chars):
            if fixed == root:
                continue
            color = Fore.GREEN if score >= 5 else Fore.YELLOW if score >= 0 else Fore.RED
            print(f"  [{cur_enc:>9}->{act_enc:<9}] {score:>4} : {color}{fixed}{Style.RESET_ALL}")

        for name in files:
            for score, cur_enc, act_enc, fixed in process_item(name, global_scores, global_examples, common_chars):
                if fixed == name:
                    continue
                color = Fore.GREEN if score >= 5 else Fore.YELLOW if score >= 0 else Fore.RED
                print(f"  [{cur_enc:>9}->{act_enc:<9}] {score:>4} : {color}{fixed}{Style.RESET_ALL}")

        for name in dirs:
            for score, cur_enc, act_enc, fixed in process_item(name, global_scores, global_examples, common_chars):
                if fixed == name:
                    continue
                color = Fore.GREEN if score >= 5 else Fore.YELLOW if score >= 0 else Fore.RED
                print(f"  [{cur_enc:>9}->{act_enc:<9}] {score:>4} : {color}{fixed}{Style.RESET_ALL}")

    print("\n============================")
    print("【总体候选转换统计】")
    sorted_global = sorted(global_scores.items(), key=lambda x: x[1], reverse=True)[:10]
    for idx, ((cur_enc, act_enc), total_score) in enumerate(sorted_global, 1):
        print(f"\n{idx:>2}. [{cur_enc:>9}->{act_enc:<9}] {total_score}")
        examples = random.sample(global_examples[(cur_enc, act_enc)], min(3, len(global_examples[(cur_enc, act_enc)])))
        for orig, fixed in examples:
            print(f"     {orig}  =>  {fixed}")
    print("============================\n")

    if sorted_global:
        choice = input("输入要使用的方案编号 (1-10)；其他任意键取消: ").strip()
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(sorted_global):
                cur_enc, act_enc = sorted_global[idx - 1][0]
                print(f"开始修复，方案 [{cur_enc}->{act_enc}]")
                logger.info(f"Chosen conversion {cur_enc}->{act_enc}")
                fixed_files, fixed_dirs = fix_mode(directory, cur_enc, act_enc)
                print(f"修复完成：文件 {fixed_files} 个，目录 {fixed_dirs} 个")
                logger.info(f"Fix done: files {fixed_files}, dirs {fixed_dirs}")
            else:
                print("取消修复")
        else:
            print("取消修复")


def fix_mode(directory, current_enc, actual_enc):
    directory = os.path.abspath(directory)
    fixed_files = 0
    fixed_dirs = 0

    root_dir_name = os.path.basename(directory)
    fixed_root_name = get_fixed_name(root_dir_name, current_enc, actual_enc)
    if fixed_root_name and fixed_root_name != root_dir_name:
        parent_dir = os.path.dirname(directory)
        new_root_path = os.path.join(parent_dir, fixed_root_name)
        if not os.path.exists(new_root_path):
            try:
                os.rename(directory, new_root_path)
                logger.info(f"Rename dir {directory} -> {new_root_path}")
                directory = new_root_path
            except Exception as e:
                logger.error(f"Rename root fail: {e}")

    for root, dirs, files in os.walk(directory, topdown=False):
        for name in dirs:
            new_name = get_fixed_name(name, current_enc, actual_enc)
            if not new_name or new_name == name:
                continue
            old_path, new_path = os.path.join(root, name), os.path.join(root, new_name)
            if not os.path.exists(new_path):
                try:
                    os.rename(old_path, new_path)
                    fixed_dirs += 1
                    logger.info(f"Dir {old_path} -> {new_path}")
                except Exception as e:
                    logger.error(f"Dir rename fail {old_path}: {e}")

        for name in files:
            new_name = get_fixed_name(name, current_enc, actual_enc)
            if not new_name or new_name == name:
                continue
            old_path, new_path = os.path.join(root, name), os.path.join(root, new_name)
            if not os.path.exists(new_path):
                try:
                    os.rename(old_path, new_path)
                    fixed_files += 1
                    logger.info(f"File {old_path} -> {new_path}")
                except Exception as e:
                    logger.error(f"File rename fail {old_path}: {e}")
    return fixed_files, fixed_dirs


def main():
    parser = argparse.ArgumentParser(description="修复文件名乱码脚本")
    parser.add_argument("--dir", default=".", help="指定目录")
    parser.add_argument("--current-enc", help="当前错误编码")
    parser.add_argument("--actual-enc", help="实际编码")
    parser.add_argument("--dict", dest="dict_file", default="dict.txt", help="常用字词典")
    args = parser.parse_args()

    if args.current_enc and args.actual_enc:
        f, d = fix_mode(args.dir, args.current_enc, args.actual_enc)
        print(f"修复完成：文件 {f} 个，目录 {d} 个")
        logger.info(f"Direct fix done: files {f}, dirs {d}")
    else:
        preview_mode(args.dir, args.dict_file)

    print("\n脚本将在 5 秒后退出...")
    time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception(f"程序报错：{e}")
        input()
