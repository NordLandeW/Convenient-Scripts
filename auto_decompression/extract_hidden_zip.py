import shutil
import sys
import os
import json
import subprocess

# 可调整参数
CHUNK_SIZE = 256 * 1024 * 1024  # 256 MB

# 用于缓存 binwalk 安装检测结果，避免重复检测
_BINWALK_INSTALLED = None

# 用于缓存针对每个文件的 binwalk 检测结果，避免重复检测
_BINWALK_RESULTS_CACHE = {}

def _check_binwalk_installed():
    """
    检查系统中是否安装了 binwalk，可根据需要修改检查方式:
    - 使用 shutil.which("binwalk")
    - 或者通过 subprocess.run(["binwalk", "--help"])
    返回 True/False，并在未安装时输出提示。
    """
    global _BINWALK_INSTALLED
    if _BINWALK_INSTALLED is not None:
        return _BINWALK_INSTALLED

    # 示例：使用 shutil.which 检测
    found = shutil.which("binwalk")
    if not found:
        print("未检测到 binwalk，可执行文件。请先安装 binwalk。")
        _BINWALK_INSTALLED = False
    else:
        _BINWALK_INSTALLED = True

    return _BINWALK_INSTALLED


def _get_binwalk_analysis(filename):
    """
    调用 binwalk 并返回解析后的 JSON 结果。
    如果 binwalk 不可用，则直接返回空列表。
    """
    if not _check_binwalk_installed():
        return []  # 无法执行 binwalk，则返回空

    global _BINWALK_RESULTS_CACHE
    if filename in _BINWALK_RESULTS_CACHE:
        return _BINWALK_RESULTS_CACHE[filename]

    # Windows 下若 binwalk.exe 在同级目录，可写:
    # cmd = [".\\binwalk.exe", filename, "-l", "-", "-q"]
    cmd = ["binwalk", filename, "-l", "-", "-q"]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        print(f"调用 binwalk 出错：{e}")
        return []

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        print("无法解析 binwalk 的 JSON 输出。")
        parsed = []

    _BINWALK_RESULTS_CACHE[filename] = parsed
    return parsed


def _pick_highest_confidence(filename, signature):
    """
    从 binwalk 的 JSON 输出中选出与 signature 匹配的最高置信度结果。
    - signature 为 "*" 时，表示可接受任何格式（name）。
    - 否则按照忽略大小写匹配格式名称。
    返回值示例: (offset, size, name, confidence)，若没找到则返回 None。
    """
    data = _get_binwalk_analysis(filename)
    if not data:
        return None

    # binwalk 的 JSON 输出通常是长度为 1 的数组
    analysis = data[0].get("Analysis", {}) if len(data) > 0 else {}
    file_map = analysis.get("file_map", [])

    candidate = None  # (offset, size, name, confidence)
    sig_lower = signature.lower()

    for item in file_map:
        item_name = str(item.get("name", "")).lower()
        conf = item.get("confidence", 0)
        off = item.get("offset", 0)
        sz = item.get("size", 0)

        # 如果 signature == "*"，表示接受任何格式；否则需精确匹配
        if (sig_lower == "*") or (item_name == sig_lower):
            # 选取 confidence 最大的
            if candidate is None or conf > candidate[3]:
                candidate = (off, sz, item_name, conf)

    return candidate


def has_embedded_signature(filename, signature):
    """
    检查 filename 文件内是否包含指定(或指定列表) signature，并进行初步有效性验证，
    若有效，则返回 True，否则返回 False。

    这里附加需求：
    - 若 binwalk 未安装，提示用户后返回 False。
    - 若最高置信度 <= 200，则也返回 False。
    """
    if not _check_binwalk_installed():
        # binwalk 不可用，直接返回 False
        return False

    chosen = _pick_highest_confidence(filename, signature)
    if chosen is None:
        return False

    offset, size, name, confidence = chosen
    # 只有当 confidence > 200 时才返回 True
    return confidence > 200


def extract_embedded_file(input_file, output_file, signature):
    """
    在符合的 signature 结果中找置信度最高的进行提取。
    注意：此处并未对 confidence 做限制，如果需要也可再加判断。
    若未找到符合的，则抛出 ValueError。
    """
    chosen = _pick_highest_confidence(input_file, signature)
    if chosen is None:
        raise ValueError(f"无法找到指定格式({signature})的嵌入文件：{input_file}")

    offset, size, name, conf = chosen
    if size <= 0:
        raise ValueError(f"从 binwalk 中解析得到的 size={size} 不合法，无法提取。")

    with open(input_file, 'rb') as f_in, open(output_file, 'wb') as f_out:
        f_in.seek(offset)
        bytes_left = size
        while bytes_left > 0:
            chunk_size = min(CHUNK_SIZE, bytes_left)
            data = f_in.read(chunk_size)
            if not data:
                break
            f_out.write(data)
            bytes_left -= len(data)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使用方法: python split_combined_file.py <文件1> <文件2> ...")
        sys.exit(1)

    for input_file in sys.argv[1:]:
        try:
            if has_embedded_signature(input_file, "zip"):
                output_zip = f"{input_file}_embedded.zip"
                extract_embedded_file(input_file, output_zip, "zip")
                print(f"成功提取 ZIP 文件: {output_zip}")
            elif has_embedded_signature(input_file, "rar"):
                output_rar = f"{input_file}_embedded.rar"
                extract_embedded_file(input_file, output_rar, "rar")
                print(f"成功提取 RAR 文件: {output_rar}")
            else:
                print(f"{input_file} 中未发现 (confidence > 200) 的 ZIP 或 RAR 文件。")
        except Exception as e:
            print(f"处理文件 {input_file} 时出错: {e}")
