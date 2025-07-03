import shutil
import sys
import os
import json
import subprocess
import re

# 可调整参数
CHUNK_SIZE = 256 * 1024 * 1024  # 256 MB

# 搜索方式控制变量
USE_BINWALK = False  # True: 使用 binwalk 搜索, False: 使用手写搜索

# 文件魔法头定义
MAGIC_SIGNATURES = {
    "zip": b"PK\x03\x04",      # ZIP文件的魔法头
    "rar": b"Rar!\x1a\x07",   # RAR文件的魔法头 (RAR v4)
    "rar5": b"Rar!\x1a\x07\x01\x00",  # RAR5文件的魔法头
    "7z": b"7z\xbc\xaf\x27\x1c",      # 7Z文件的魔法头
    "tar": b"ustar\x00",       # TAR文件的魔法头 (位置在257字节处)
    "gz": b"\x1f\x8b",         # GZIP文件的魔法头
    "bz2": b"BZ",              # BZIP2文件的魔法头
    "xz": b"\xfd7zXZ\x00",     # XZ文件的魔法头
    "pdf": b"%PDF-",           # PDF文件的魔法头
    "png": b"\x89PNG\r\n\x1a\n",  # PNG文件的魔法头
    "jpg": b"\xff\xd8\xff",    # JPEG文件的魔法头
    "gif": b"GIF8",            # GIF文件的魔法头
    "exe": b"MZ",              # Windows PE可执行文件的魔法头
    "elf": b"\x7fELF",         # Linux ELF可执行文件的魔法头
}

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

    根据 USE_BINWALK 变量决定使用哪种搜索方式：
    - True: 使用 binwalk 搜索（若 binwalk 未安装，提示用户后返回 False；若最高置信度 <= 200，则也返回 False）
    - False: 使用手写搜索（直接查找魔法头）
    """
    if USE_BINWALK:
        # 使用 binwalk 搜索
        if not _check_binwalk_installed():
            # binwalk 不可用，直接返回 False
            return False

        chosen = _pick_highest_confidence(filename, signature)
        if chosen is None:
            return False

        offset, size, name, confidence = chosen
        # 只有当 confidence > 200 时才返回 True
        return confidence > 200
    else:
        # 使用手写搜索
        # 支持 RAR5 的特殊处理
        if signature.lower() == "rar":
            # 先尝试 RAR5，再尝试 RAR4
            return (_find_first_magic_signature(filename, "rar5") is not None or 
                   _find_first_magic_signature(filename, "rar") is not None)
        else:
            return _find_first_magic_signature(filename, signature.lower()) is not None


def _find_first_magic_signature(input_file, signature_type):
    """
    在文件中查找指定类型的第一个魔法头，并返回其偏移量。
    如果找不到，则返回 None。
    
    Args:
        input_file: 输入文件路径
        signature_type: 签名类型，如 "zip", "rar", "7z" 等
    
    Returns:
        int: 魔法头的偏移量，如果找不到则返回 None
    """
    if signature_type not in MAGIC_SIGNATURES:
        return None
    
    magic_bytes = MAGIC_SIGNATURES[signature_type]
    
    with open(input_file, 'rb') as f:
        # 特殊处理：TAR文件的魔法头在第257字节处
        if signature_type == "tar":
            f.seek(257)  # TAR魔法头位置
            data = f.read(len(magic_bytes))
            if data == magic_bytes:
                return 257
            else:
                return None
        
        # 对于其他文件类型，在整个文件中搜索
        # 分块读取以处理大文件
        offset = 0
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            
            pos = chunk.find(magic_bytes)
            if pos != -1:
                return offset + pos
            
            # 处理跨块边界的情况
            # 保留最后几个字节以防魔法头被分割
            if len(chunk) == CHUNK_SIZE:
                # 回退魔法头长度-1个字节，以防跨块
                f.seek(f.tell() - len(magic_bytes) + 1)
                offset = f.tell()
            else:
                break
        
        return None

def extract_embedded_file(input_file, output_file, signature):
    """
    根据 USE_BINWALK 变量决定使用哪种提取方式：
    - True: 使用 binwalk 分析结果进行提取
    - False: 使用手写搜索进行提取
    
    对于手写搜索，支持多种文件格式的提取。
    若未找到符合的，则抛出 ValueError。
    """
    if USE_BINWALK:
        # 使用 binwalk 方式提取
        # 对ZIP文件的特殊处理
        if signature.lower() == "zip":
            # 尝试直接查找ZIP文件的魔法头
            offset = _find_first_magic_signature(input_file, "zip")
            if offset is not None:
                # 找到了ZIP魔法头，从这里开始提取到文件末尾
                file_size = os.path.getsize(input_file)
                size = file_size - offset
                
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
                    
                    return  # 成功提取，直接返回
        
        # 如果不是ZIP文件或者ZIP特殊处理失败，则使用原来的方法
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
    else:
        # 使用手写搜索提取
        offset = None
        signature_lower = signature.lower()
        
        # 处理 RAR 文件的特殊情况（RAR5 和 RAR4）
        if signature_lower == "rar":
            offset = _find_first_magic_signature(input_file, "rar5")
            if offset is None:
                offset = _find_first_magic_signature(input_file, "rar")
        else:
            offset = _find_first_magic_signature(input_file, signature_lower)
        
        if offset is None:
            raise ValueError(f"无法找到指定格式({signature})的嵌入文件：{input_file}")
        
        # 从找到的偏移量开始提取到文件末尾
        file_size = os.path.getsize(input_file)
        size = file_size - offset
        
        if size <= 0:
            raise ValueError(f"计算得到的提取大小({size})不合法，无法提取。")
        
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
        print("使用方法: python extract_hidden_zip.py <文件1> <文件2> ...")
        print(f"当前搜索模式: {'binwalk' if USE_BINWALK else '手写搜索'}")
        print("支持的文件格式: ZIP, RAR, 7Z, TAR, GZ, BZ2, XZ, PDF, PNG, JPG, GIF, EXE, ELF")
        sys.exit(1)

    print(f"使用搜索模式: {'binwalk' if USE_BINWALK else '手写搜索'}")
    
    # 支持的文件格式列表
    supported_formats = ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "pdf", "png", "jpg", "gif", "exe", "elf"]
    
    for input_file in sys.argv[1:]:
        print(f"\n处理文件: {input_file}")
        found_any = False
        
        try:
            for fmt in supported_formats:
                if has_embedded_signature(input_file, fmt):
                    # 根据文件格式确定输出文件扩展名
                    if fmt == "jpg":
                        ext = "jpeg"
                    else:
                        ext = fmt
                    
                    output_file = f"{input_file}_embedded.{ext}"
                    extract_embedded_file(input_file, output_file, fmt)
                    print(f"成功提取 {fmt.upper()} 文件: {output_file}")
                    found_any = True
                    break  # 找到第一个匹配的格式就停止
            
            if not found_any:
                condition_text = "(confidence > 200)" if USE_BINWALK else ""
                print(f"{input_file} 中未发现 {condition_text} 支持的嵌入文件格式。")
                
        except Exception as e:
            print(f"处理文件 {input_file} 时出错: {e}")
