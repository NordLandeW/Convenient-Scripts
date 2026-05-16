#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文件名修复脚本

当前版本重点支持：
1. 默认 dry-run，只有指定 --apply 才真正改名
2. 文件 / 目录分开处理：--target files|dirs|both
3. 文件名 stem 与扩展名分开处理，默认不动扩展名
4. 对每个名称独立选择最优编码方案，而不是整棵树强制使用同一方案
5. 支持常见日文压缩包乱码：GBK/GB18030/CP936 误解 Shift-JIS/CP932
6. 生成 JSONL rename plan，可用于审查和后续撤销
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from colorama import Fore, Style, init
from loguru import logger

init(autoreset=True)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
logger.remove()  # 避免批量 apply 时把数千条 rename 日志刷到终端；详细记录写入文件。
logger.add(os.path.join(SCRIPT_DIR, "fix_filename.log"), rotation="10 MB", encoding="utf-8", enqueue=True)

# 这里的含义是：当前错误文本能被 current_enc 重新编码成原始字节，
# 然后这些字节按 actual_enc 解码得到更正确的文本。
ENCODING_PAIRS = [
    ("gbk", "cp932"),
    ("gb18030", "cp932"),
    ("cp936", "cp932"),
    ("gbk", "shift_jis"),
    ("gb18030", "shift_jis"),
    ("cp936", "shift_jis"),
    ("latin1", "utf-8"),
    ("cp1252", "utf-8"),
    ("cp437", "gbk"),
    ("cp850", "gbk"),
    ("cp932", "gbk"),
    ("shift_jis", "gbk"),
    ("gbk", "utf-8"),
    ("gbk", "utf-8", "gbk", "cp932"),
    ("gb18030", "utf-8"),
    ("big5", "utf-8"),
    ("cp950", "utf-8"),
]

WINDOWS_INVALID_CHARS = set('<>:"/\\|?*')
MOJIBAKE_HINT_CHARS = set(
    "ÃÂÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞß"
    "àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ"
    "¤½¼¾€™"
)
# 常见“日文 Shift-JIS 字节被 GBK/Big5 误解”的罕见 CJK 片段。
JP_MOJIBAKE_CJK_HINTS = set("偁傝偡偺傞傟僀僂僃僄僉僋僌働僎僔僗僙僜僞僠僢僣僤僥僨僩僫僯僱僶僷僾儀儁儂儃寢忛惏嘦垷旤恀瀵㈠繘鎯忓槮鐑村牅鎼峰閻戞潙鐗呴幖宄帮細愮巬嘨")


@dataclass
class Candidate:
    fixed: str
    current_enc: str
    actual_enc: str
    score_before: float
    score_after: float
    gain: float
    confidence: float
    reason: str


@dataclass
class RenamePlan:
    kind: str
    old_path: str
    new_path: str
    old_name: str
    new_name: str
    current_enc: str
    actual_enc: str
    gain: float
    confidence: float
    reason: str


def is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
    )


def is_hiragana(ch: str) -> bool:
    return 0x3040 <= ord(ch) <= 0x309F


def is_katakana(ch: str) -> bool:
    code = ord(ch)
    return 0x30A0 <= code <= 0x30FF or 0xFF65 <= code <= 0xFF9F


def is_halfwidth_katakana(ch: str) -> bool:
    return 0xFF65 <= ord(ch) <= 0xFF9F


def is_private_use(ch: str) -> bool:
    return 0xE000 <= ord(ch) <= 0xF8FF


def is_hangul(ch: str) -> bool:
    return 0xAC00 <= ord(ch) <= 0xD7AF


def load_common_chars(dict_file: str | None) -> set[str]:
    if not dict_file:
        return set()
    dict_path = os.path.join(SCRIPT_DIR, dict_file) if not os.path.isabs(dict_file) else dict_file
    if not os.path.exists(dict_path):
        return set()
    try:
        with open(dict_path, "r", encoding="utf-8") as f:
            chars = set(ch for ch in f.read() if not ch.isspace())
        logger.info(f"Loaded dict {dict_path} with {len(chars)} chars")
        return chars
    except Exception as e:
        logger.error(f"Dict load error: {e}")
        return set()


def get_fixed_name(original: str, current_enc: str, actual_enc: str, *, strict: bool = True) -> str | None:
    errors = "strict" if strict else "replace"
    try:
        return original.encode(current_enc, errors=errors).decode(actual_enc, errors=errors)
    except Exception:
        return None


def get_fixed_name_chain(original: str, chain: Sequence[str], *, strict: bool = True) -> str | None:
    if len(chain) < 2 or len(chain) % 2 != 0:
        return None
    errors = "strict" if strict else "replace"
    text = original
    try:
        for enc, dec in zip(chain[0::2], chain[1::2]):
            text = text.encode(enc, errors=errors).decode(dec, errors=errors)
        return text
    except Exception:
        return None


def contains_cjk_compatibility(text: str) -> bool:
    return any(0xF900 <= ord(ch) <= 0xFAFF for ch in text)


def score_text(text: str, common_chars: set[str] | None = None) -> float:
    """给文本自然度打分。分数不是绝对语言模型，只用于比较转换前后增益。"""
    score = 0.0
    common_chars = common_chars or set()

    for ch in text:
        code = ord(ch)
        if ch in common_chars:
            score += 2.0
        if is_hiragana(ch):
            score += 6.0
        elif is_halfwidth_katakana(ch):
            score -= 6.0
        elif is_katakana(ch):
            score += 4.0
        elif is_hangul(ch):
            score += 3.0
        elif is_cjk(ch):
            score += -8.0 if 0xF900 <= code <= 0xFAFF else 1.0
        elif ch.isascii() and (ch.isalnum() or ch in " ._-+[](){}#@&!,"):
            score += 0.35

        if ch in JP_MOJIBAKE_CJK_HINTS:
            score -= 10.0
        if ch in MOJIBAKE_HINT_CHARS:
            score -= 8.0
        if is_private_use(ch):
            score -= 80.0
        if ch in WINDOWS_INVALID_CHARS:
            score -= 100.0
        if ch in "\ufffd?":
            score -= 30.0
        if code < 32:
            score -= 100.0

    # 连续全角日文假名通常比随机 CJK 更像可读日文文件名；半角假名常是二次误转结果，不加分。
    if re.search(r"[\u3040-\u30ff]{2,}", text):
        score += 12.0
    # 连续罕见 CJK mojibake 强烈扣分。
    if re.search("[" + re.escape("".join(JP_MOJIBAKE_CJK_HINTS)) + r"]{2,}", text):
        score -= 20.0

    return score


def safe_splitext(name: str) -> tuple[str, str]:
    stem, ext = os.path.splitext(name)
    # .bashrc 这类名字不把整体当扩展名。
    if not stem:
        return name, ""
    return stem, ext


def looks_suspicious_source(text: str) -> bool:
    """判断源文本是否像乱码。避免把正常中文/日文名反复转换成半角片假名等伪高分结果。"""
    if any(ch in JP_MOJIBAKE_CJK_HINTS or ch in MOJIBAKE_HINT_CHARS for ch in text):
        return True
    cjk_count = sum(1 for ch in text if is_cjk(ch))
    kana_count = sum(1 for ch in text if is_hiragana(ch) or is_katakana(ch))
    # 文件序号 001/101 等不应降低可疑度；只把 ASCII 字母和少数符号视作“正常上下文”。
    ascii_context_count = sum(1 for ch in text if ch.isascii() and not ch.isdigit())
    # 大量 CJK 但没有假名/ASCII 上下文，可能是 SJIS 被 GBK 误解后的汉字串；但短正常中文名如“中谷育”不应触发。
    return cjk_count >= 4 and kana_count == 0 and ascii_context_count <= 2


def is_bad_fixed_text(text: str) -> bool:
    if any(is_private_use(ch) for ch in text):
        return True
    if sum(1 for ch in text if is_halfwidth_katakana(ch)) >= 2:
        return True
    return False



def context_match_score(text: str, context_parts: Sequence[str] | None) -> int:
    """返回转换结果与父目录上下文的匹配强度。

    许多短文件名前缀（如二字角色名）单靠字符自然度无法胜过原乱码，
    但如果转换结果中的连续片段出现在父目录/祖先目录中，就很可能是正确修复。
    该函数只看文本片段，不绑定任何特定目录名。
    """
    if not context_parts:
        return 0

    core = "".join(ch for ch in text if not ch.isascii() or ch.isalpha())
    if len(core) < 2:
        return 0

    score = 0
    for part in context_parts:
        best = 0
        max_len = min(6, len(core))
        for size in range(max_len, 1, -1):
            if best:
                break
            for start in range(0, len(core) - size + 1):
                token = core[start:start + size]
                if token in part:
                    best = size
                    break
        score += best
    return score


def build_candidates_for_text(
    text: str,
    common_chars: set[str] | None,
    pairs: Iterable[tuple[str, ...]],
    min_gain: float,
    context_parts: Sequence[str] | None = None,
) -> list[Candidate]:
    source_suspicious = looks_suspicious_source(text)
    before = score_text(text, common_chars)
    candidates: list[Candidate] = []

    for chain in pairs:
        fixed = get_fixed_name_chain(text, chain, strict=True)
        if not fixed or fixed == text or is_bad_fixed_text(fixed):
            continue

        ctx_score = context_match_score(fixed, context_parts)
        # 没有可疑源特征时，只有“转换结果明显匹配父目录上下文”才允许进入评分。
        # 这让短角色名能被泛化修复，同时避免把正常可读名随意重编码。
        if not source_suspicious and ctx_score < 2:
            continue

        after = score_text(fixed, common_chars)
        gain = after - before
        if contains_cjk_compatibility(text) and not contains_cjk_compatibility(fixed):
            gain += 12.0
        if ctx_score >= 2:
            gain += 8.0 * ctx_score
        if gain < min_gain:
            continue
        confidence = max(0.0, min(0.99, gain / max(20.0, abs(before) + 20.0)))
        cur_enc = "+".join(chain[0::2])
        act_enc = "+".join(chain[1::2])
        chain_text = "->".join(chain)
        reason = f"{chain_text}, score {before:.1f}->{after:.1f}"
        if ctx_score:
            reason += f", context={ctx_score}"
        candidates.append(
            Candidate(
                fixed=fixed,
                current_enc=cur_enc,
                actual_enc=act_enc,
                score_before=before,
                score_after=after,
                gain=gain,
                confidence=confidence,
                reason=reason,
            )
        )

    # 多层乱码链有时会先产生一个“看似不更差”的中间乱码（例如 gbk->utf-8），
    # 在评分完全相同的情况下优先选择更完整的链，避免停在半修复状态。
    candidates.sort(key=lambda c: (c.gain, c.confidence, c.current_enc.count("+")), reverse=True)
    return candidates


def choose_best_name(
    name: str,
    common_chars: set[str] | None,
    pairs: Iterable[tuple[str, ...]],
    min_gain: float,
    preserve_ext: bool = True,
    context_parts: Sequence[str] | None = None,
) -> Candidate | None:
    if preserve_ext:
        stem, ext = safe_splitext(name)
        candidates = build_candidates_for_text(stem, common_chars, pairs, min_gain, context_parts=context_parts)
        if not candidates:
            return None
        best = candidates[0]
        best.fixed = best.fixed + ext
        return best

    candidates = build_candidates_for_text(name, common_chars, pairs, min_gain, context_parts=context_parts)
    return candidates[0] if candidates else None


def unique_target_path(path: str, collision: str) -> str | None:
    if not os.path.exists(path):
        return path
    if collision == "skip":
        return None
    if collision != "suffix":
        return None

    root, ext = os.path.splitext(path)
    for i in range(1, 10000):
        candidate = f"{root} ({i}){ext}"
        if not os.path.exists(candidate):
            return candidate
    return None


def collect_plans(
    directory: str,
    target: str,
    common_chars: set[str],
    pairs: list[tuple[str, ...]],
    min_gain: float,
    preserve_ext: bool,
    collision: str,
) -> list[RenamePlan]:
    directory = os.path.abspath(directory)
    plans: list[RenamePlan] = []

    for root, dirs, files in os.walk(directory, topdown=False):
        if target in ("dirs", "both"):
            for name in dirs:
                cand = choose_best_name(name, common_chars, pairs, min_gain, preserve_ext=False, context_parts=Path(root).parts)
                if not cand:
                    continue
                old_path = os.path.join(root, name)
                new_path = unique_target_path(os.path.join(root, cand.fixed), collision)
                if not new_path:
                    logger.warning(f"Skip dir due to collision: {old_path} -> {os.path.join(root, cand.fixed)}")
                    continue
                plans.append(
                    RenamePlan("dir", old_path, new_path, name, os.path.basename(new_path), cand.current_enc,
                               cand.actual_enc, cand.gain, cand.confidence, cand.reason)
                )

        if target in ("files", "both"):
            for name in files:
                cand = choose_best_name(name, common_chars, pairs, min_gain, preserve_ext=preserve_ext, context_parts=Path(root).parts)
                if not cand:
                    continue
                old_path = os.path.join(root, name)
                new_path = unique_target_path(os.path.join(root, cand.fixed), collision)
                if not new_path:
                    logger.warning(f"Skip file due to collision: {old_path} -> {os.path.join(root, cand.fixed)}")
                    continue
                plans.append(
                    RenamePlan("file", old_path, new_path, name, os.path.basename(new_path), cand.current_enc,
                               cand.actual_enc, cand.gain, cand.confidence, cand.reason)
                )

    return plans


def default_plan_path(prefix: str = "fixname-plan") -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.join(SCRIPT_DIR, f"{prefix}-{stamp}.jsonl")
    if not os.path.exists(base):
        return base
    for i in range(1, 1000):
        candidate = os.path.join(SCRIPT_DIR, f"{prefix}-{stamp}-{i}.jsonl")
        if not os.path.exists(candidate):
            return candidate
    raise RuntimeError(f"无法生成不冲突的日志路径：{base}")


def write_plan(plans: list[RenamePlan], plan_path: str | None) -> str:
    if not plan_path:
        plan_path = default_plan_path("fixname-plan")
    with open(plan_path, "w", encoding="utf-8") as f:
        for plan in plans:
            f.write(json.dumps(asdict(plan), ensure_ascii=False) + "\n")
    return plan_path


def read_plan(plan_path: str) -> list[RenamePlan]:
    plans: list[RenamePlan] = []
    with open(plan_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            plans.append(RenamePlan(**json.loads(line)))
    return plans


def write_run_log(
    plans: list[RenamePlan],
    results: list[dict],
    log_path: str | None,
    *,
    undo: bool = False,
    source_log: str | None = None,
) -> str:
    if not log_path:
        log_path = default_plan_path("fixname-undo" if not undo else "fixname-redo")
    payload = {
        "version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "undo" if undo else "apply",
        "source_log": source_log,
        "summary": {
            "total": len(results),
            "renamed": sum(1 for r in results if r["status"] == "renamed"),
            "skipped": sum(1 for r in results if r["status"] != "renamed"),
            "files": sum(1 for r in results if r["status"] == "renamed" and r["kind"] == "file"),
            "dirs": sum(1 for r in results if r["status"] == "renamed" and r["kind"] == "dir"),
        },
        "operations": results,
        "plans": [asdict(plan) for plan in plans],
    }
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return log_path


def read_run_log(log_path: str) -> list[RenamePlan]:
    text = Path(log_path).read_text(encoding="utf-8").lstrip()
    if not text:
        return []
    if text.startswith("{"):
        payload = json.loads(text)
        if "plans" in payload:
            return [RenamePlan(**item) for item in payload["plans"]]
        if "operations" in payload:
            plans: list[RenamePlan] = []
            for item in payload["operations"]:
                plan_data = item.get("plan")
                if plan_data:
                    plans.append(RenamePlan(**plan_data))
            return plans
    return read_plan(log_path)


def apply_plans(plans: list[RenamePlan], *, undo: bool = False) -> tuple[int, int, list[dict]]:
    fixed_files = 0
    fixed_dirs = 0
    results: list[dict] = []
    iterable = list(reversed(plans)) if undo else plans
    for plan in iterable:
        src = plan.new_path if undo else plan.old_path
        dst = plan.old_path if undo else plan.new_path
        result = {
            "kind": plan.kind,
            "old_path": plan.old_path,
            "new_path": plan.new_path,
            "src": src,
            "dst": dst,
            "status": "pending",
            "error": None,
            "plan": asdict(plan),
        }
        if not os.path.exists(src):
            result["status"] = "missing-source"
            logger.warning(f"Missing source, skip: {src}")
            results.append(result)
            continue
        if os.path.exists(dst):
            result["status"] = "target-exists"
            logger.warning(f"Target exists, skip: {dst}")
            results.append(result)
            continue
        try:
            os.rename(src, dst)
            result["status"] = "renamed"
            if plan.kind == "file":
                fixed_files += 1
            else:
                fixed_dirs += 1
            action = "Undo" if undo else plan.kind
            logger.info(f"{action} {src} -> {dst}")
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            logger.error(f"Rename fail {src}: {e}")
        results.append(result)
    return fixed_files, fixed_dirs, results


def print_plans(plans: list[RenamePlan], limit: int) -> None:
    if not plans:
        print("未发现高置信度可修复项目。")
        return

    print(f"发现 {len(plans)} 个候选改名：")
    for idx, plan in enumerate(plans[:limit], 1):
        color = Fore.GREEN if plan.confidence >= 0.6 else Fore.YELLOW
        print(
            f"{idx:>4}. [{plan.kind}] {plan.current_enc}->{plan.actual_enc} "
            f"gain={plan.gain:.1f} conf={plan.confidence:.2f}"
        )
        print(f"      {plan.old_name}")
        print(f"   => {color}{plan.new_name}{Style.RESET_ALL}")
    if len(plans) > limit:
        print(f"... 其余 {len(plans) - limit} 项已省略，可查看 plan 文件。")


def parse_pairs(args: argparse.Namespace) -> list[tuple[str, ...]]:
    if args.current_enc and args.actual_enc:
        return [(args.current_enc, args.actual_enc)]
    return list(dict.fromkeys(ENCODING_PAIRS))


def main() -> None:
    parser = argparse.ArgumentParser(description="启发式修复文件/目录名乱码，默认只 dry-run")
    parser.add_argument("--dir", default=".", help="指定目录")
    parser.add_argument("--current-enc", help="当前错误编码，例如 gbk")
    parser.add_argument("--actual-enc", help="实际编码，例如 cp932")
    parser.add_argument("--dict", dest="dict_file", default="dict.txt", help="常用字词典")
    parser.add_argument("--target", choices=["files", "dirs", "both"], default="files", help="处理文件、目录或两者")
    parser.add_argument("--apply", action="store_true", help="真正执行改名；未指定时只 dry-run")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不改名；为默认行为")
    parser.add_argument("--undo", help="读取 JSON/JSONL 修正日志并反向撤销")
    parser.add_argument("--log", help="实际 apply/undo 的 JSON 修正日志输出路径")
    parser.add_argument("--min-gain", type=float, default=4.0, help="转换后评分至少提升多少才列入计划")
    parser.add_argument("--no-preserve-ext", action="store_true", help="文件名转换时连扩展名也一起尝试转换")
    parser.add_argument("--collision", choices=["skip", "suffix"], default="skip", help="目标重名时跳过或追加编号")
    parser.add_argument("--plan", help="JSONL 改名计划输出路径")
    parser.add_argument("--limit", type=int, default=80, help="屏幕最多展示多少条候选")
    parser.add_argument("--pause", action="store_true", help="结束前等待 5 秒，方便双击运行时查看输出")
    args = parser.parse_args()

    if args.undo:
        plans = read_run_log(args.undo)
        if args.apply:
            fixed_files, fixed_dirs, results = apply_plans(plans, undo=True)
            log_path = write_run_log(plans, results, args.log, undo=True, source_log=args.undo)
            print(f"撤销完成：文件 {fixed_files} 个，目录 {fixed_dirs} 个")
            print(f"撤销日志已写入：{log_path}")
        else:
            print(f"将从日志/计划中撤销 {len(plans)} 个改名；当前为 dry-run，未修改任何文件。追加 --apply 才会执行。")
        return

    common_chars = load_common_chars(args.dict_file)
    pairs = parse_pairs(args)
    plans = collect_plans(
        args.dir,
        args.target,
        common_chars,
        pairs,
        args.min_gain,
        preserve_ext=not args.no_preserve_ext,
        collision=args.collision,
    )

    print_plans(plans, args.limit)
    plan_path = write_plan(plans, args.plan)
    print(f"\n改名计划已写入：{plan_path}")

    if args.apply:
        fixed_files, fixed_dirs, results = apply_plans(plans)
        log_path = write_run_log(plans, results, args.log)
        print(f"修复完成：文件 {fixed_files} 个，目录 {fixed_dirs} 个")
        print(f"修正日志已写入：{log_path}")
        logger.info(f"Apply done: files {fixed_files}, dirs {fixed_dirs}, plan={plan_path}, log={log_path}")
    else:
        print("当前为 dry-run，未修改任何文件。确认无误后可追加 --apply 执行。")

    if args.pause:
        print("\n脚本将在 5 秒后退出...")
        time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception(f"程序报错：{e}")
        raise
