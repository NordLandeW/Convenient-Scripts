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
from collections import defaultdict
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


def update_global_stats(key, score, orig, fixed,
                        global_scores, global_examples, global_best):
    global_scores[key] += score
    # 保存 (score, orig, fixed)
    global_examples[key].append((score, orig, fixed))
    if score > global_best[key][0]:
        global_best[key] = (score, orig, fixed)



def process_item(name, global_scores, global_examples, global_best, common_chars):
    candidates = []
    for cur_enc in CANDIDATE_ENCODINGS:
        for act_enc in CANDIDATE_ENCODINGS:
            fixed = get_fixed_name(name, cur_enc, act_enc)
            if fixed is None:
                continue
            score = score_conversion(name, fixed, common_chars)
            candidates.append((score, cur_enc, act_enc, fixed))
            key = (cur_enc, act_enc)
            update_global_stats(key, score, name, fixed, global_scores, global_examples, global_best)
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates


def print_candidates(candidates):
    for score, cur_enc, act_enc, fixed in candidates:
        if score < 0:
            break  # 忽略负分
        color = Fore.GREEN if score >= 5 else Fore.YELLOW
        print(f"  [{cur_enc:>9}->{act_enc:<9}] {score:>4} : {color}{fixed}{Style.RESET_ALL}")


def preview_mode(directory, dict_file):
    dict_used = False
    common_chars = None
    dict_path = os.path.join(SCRIPT_DIR, dict_file) if not os.path.isabs(dict_file) else dict_file
    if dict_file and os.path.exists(dict_path):
        try:
            with open(dict_path, "r", encoding="utf-8") as f:
                common_chars = set(ch for ch in f.read() if not ch.isspace())
            dict_used = True
            print(f"加载常用字词典：{dict_path}，{len(common_chars)} 字符")
            logger.info(f"Loaded dict {dict_path} with {len(common_chars)} chars")
        except Exception as e:
            print(f"{Fore.RED}加载字典失败：{e}{Style.RESET_ALL}")
            logger.error(f"Dict load error: {e}")

    global_scores = defaultdict(int)
    global_examples = defaultdict(list)
    global_best = defaultdict(lambda: (-1000, "", ""))

    total_files = 0
    total_dirs = 0
    all_names = set()

    # Score path candidates on names relative to the scan root to avoid bias from absolute prefixes.
    start_dir = os.path.abspath(directory)

    for root, dirs, files in os.walk(start_dir):
        print(f"\n【路径】：{root}")

        rel_root = os.path.relpath(root, start_dir)
        name_for_scoring = os.path.basename(root) if rel_root == "." else rel_root

        # Use relative segment(s); when '.', fall back to basename for meaningful scoring.
        target_for_scoring = name_for_scoring if name_for_scoring and name_for_scoring != "." else os.path.basename(root)
        if target_for_scoring:
            all_names.add(target_for_scoring)
            print_candidates(process_item(target_for_scoring, global_scores, global_examples, global_best, common_chars)[:5])

        for name in files:
            total_files += 1
            all_names.add(name)
            print_candidates(process_item(name, global_scores, global_examples, global_best, common_chars)[:3])

        for name in dirs:
            total_dirs += 1
            all_names.add(name)
            print_candidates(process_item(name, global_scores, global_examples, global_best, common_chars)[:3])

    print("\n============================")
    print("【总体候选转换统计】")
    sorted_global = sorted(global_scores.items(), key=lambda x: x[1], reverse=True)

    # 统计输出
    print(f"\n总文件数: {total_files} , 总目录数: {total_dirs}")
    print(f"是否使用字典: {'是' if dict_used else '否'}")
    print("============================\n")

    if not sorted_global:
        print("未生成任何转换候选，终止。")
        return

    # 分数最高方案
    top_key, top_score = sorted_global[0]
    cur_enc, act_enc = top_key
    print(f"分数最高方案: [{cur_enc}->{act_enc}] 累计分数 {top_score}")

    # 取该方案贡献最高的前 3 条示例
    for s, o, f in sorted(global_examples[top_key], key=lambda x: x[0],
                        reverse=True)[:3]:
        print(f"示例: {o}  =>  {f}  (分数 {s})")
    print()

    use_top = input("是否直接使用该方案? (y/N): ").strip().lower() == "y"
    if use_top:
        print(f"开始修复，方案 [{cur_enc}->{act_enc}]")
        logger.info(f"Chosen top conversion {cur_enc}->{act_enc}")
        fixed_files, fixed_dirs = fix_mode(directory, cur_enc, act_enc)
        print(f"修复完成：文件 {fixed_files} 个，目录 {fixed_dirs} 个")
        logger.info(f"Fix done: files {fixed_files}, dirs {fixed_dirs}")
        return

    # 展示前十方案供选择
    print("前十方案列表：")
    for idx, ((cur_enc, act_enc), total_score) in enumerate(sorted_global[:10], 1):
        print(f"\n{idx:>2}. [{cur_enc:>9}->{act_enc:<9}] {total_score}")
        # 取当前方案贡献最高的前 3 条示例
        for s, o, f in sorted(global_examples[(cur_enc, act_enc)],
                            key=lambda x: x[0], reverse=True)[:3]:
            print(f"     {o}  =>  {f}  (分数 {s})")

    print("\n m. 手动输入编码方案（形如 gbk->utf-8）用于自定义预览/修复")

    choice = input("输入要使用的方案编号 (1-10) 或输入 m 进行手动；其他任意键取消: ").strip()
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= min(10, len(sorted_global)):
            cur_enc, act_enc = sorted_global[idx - 1][0]
            print(f"开始修复，方案 [{cur_enc}->{act_enc}]")
            logger.info(f"Chosen conversion {cur_enc}->{act_enc}")
            fixed_files, fixed_dirs = fix_mode(directory, cur_enc, act_enc)
            print(f"修复完成：文件 {fixed_files} 个，目录 {fixed_dirs} 个")
            logger.info(f"Fix done: files {fixed_files}, dirs {fixed_dirs}")
        else:
            print("取消修复")
    elif choice.lower() == "m":
        enc_pair = input("请输入编码方案（形如 gbk->utf-8）: ").strip()
        if "->" not in enc_pair:
            print("格式无效，已取消")
            return
        cur_enc, act_enc = [p.strip() for p in enc_pair.split("->", 1)]

        # Preview manual pair on collected names to validate before applying.
        manual_candidates = []
        for name in list(all_names):
            fixed = get_fixed_name(name, cur_enc, act_enc)
            if not fixed or fixed == name:
                continue
            score = score_conversion(name, fixed, common_chars)
            manual_candidates.append((score, name, fixed))
        manual_candidates.sort(key=lambda x: x[0], reverse=True)

        if manual_candidates:
            print("\n手动方案预览 Top10：")
            for s, o, f in manual_candidates[:10]:
                print(f"示例: {o}  =>  {f}  (分数 {s})")
        else:
            print("手动方案没有可预览的有效转换。")

        if input("是否使用该方案? (y/N): ").strip().lower() == "y":
            print(f"开始修复，方案 [{cur_enc}->{act_enc}]")
            logger.info(f"Chosen manual conversion {cur_enc}->{act_enc}")
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
