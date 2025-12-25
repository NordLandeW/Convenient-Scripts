import shutil
import sys
import os
import json
import subprocess
import re
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    FileSizeColumn,
    TransferSpeedColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

CHUNK_SIZE = 256 * 1024 * 1024  # 256 MB
COPY_BUFFER_SIZE = 8 * 1024 * 1024  # 8 MB

console = Console()

# Search mode: True for binwalk, False for manual signature scanning
USE_BINWALK = False

MAGIC_SIGNATURES = {
    "zip": b"PK\x03\x04",
    "rar": b"Rar!\x1a\x07",
    "rar5": b"Rar!\x1a\x07\x01\x00",
    "7z": b"7z\xbc\xaf\x27\x1c",
    "tar": b"ustar\x00",  # TAR signature is at offset 257
    "gz": b"\x1f\x8b",
    "bz2": b"BZ",
    "xz": b"\xfd7zXZ\x00",
    "pdf": b"%PDF-",
    "png": b"\x89PNG\r\n\x1a\n",
    "jpg": b"\xff\xd8\xff",
    "gif": b"GIF8",
    "exe": b"MZ",
    "elf": b"\x7fELF",
}

_BINWALK_INSTALLED = None
_BINWALK_RESULTS_CACHE = {}

def _copy_range_with_progress(input_file, output_file, offset, size, signature):
    """Extracts a range from input to output file with a progress bar."""
    if size <= 0:
        raise ValueError(f"提取范围大小必须为正数喵：size={size}")
    total = size
    buffer = bytearray(COPY_BUFFER_SIZE)
    mv = memoryview(buffer)
    display_name = f"{signature.upper()} ➜ {os.path.basename(output_file)}"

    console.print(f"[cyan][b]开始提取嵌入文件：{display_name}[/b][/cyan]")

    with open(input_file, "rb") as f_in, open(output_file, "wb") as f_out:
        f_in.seek(offset)
        with Progress(
            SpinnerColumn(finished_text="✅"),
            TextColumn("[cyan][b]{task.fields[name]}[/b][/cyan]", justify="left"),
            BarColumn(),
            FileSizeColumn(),
            "•",
            TransferSpeedColumn(),
            "•",
            TimeElapsedColumn(),
            "/",
            TimeRemainingColumn(),
            transient=True,
            console=console,
        ) as progress:
            task = progress.add_task("extract", total=total, name=display_name)
            remaining = total
            while remaining > 0:
                chunk_size = COPY_BUFFER_SIZE if remaining > COPY_BUFFER_SIZE else remaining
                read_count = f_in.readinto(mv[:chunk_size])
                if read_count == 0:
                    raise IOError("在提取嵌入文件时遇到意外的 EOF 喵。")
                f_out.write(mv[:read_count])
                remaining -= read_count
                progress.update(task, advance=read_count)

    del mv
    console.print(f"[green][b]嵌入文件提取完成：{output_file}[/b][/green]")

def _check_binwalk_installed():
    """Checks if binwalk is available in the system PATH."""
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
    """Executes binwalk on the file and returns parsed JSON results."""
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
    """Selects the match with the highest confidence from binwalk output."""
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
    """Determines if a file contains an embedded archive based on the current search mode."""
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
    """Locates the first occurrence of a magic signature in a file."""
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
    """Extracts embedded data starting from a detected signature."""
    signature_lower = signature.lower()
    display_signature = signature_lower if signature_lower != "*" else "embedded"
    offset = None
    size = None

    if USE_BINWALK:
        # ZIP 优化：直接通过魔法头定位
        if signature_lower == "zip":
            offset = _find_first_magic_signature(input_file, "zip")
            if offset is not None:
                size = os.path.getsize(input_file) - offset

        if offset is None or size is None:
            chosen = _pick_highest_confidence(input_file, signature)
            if chosen is None:
                raise ValueError(f"无法找到指定格式({signature})的嵌入文件：{input_file}")

            offset, size, name, _ = chosen
            if name:
                display_signature = str(name)
    else:
        if signature_lower == "rar":
            offset = _find_first_magic_signature(input_file, "rar5")
            if offset is None:
                offset = _find_first_magic_signature(input_file, "rar")
        else:
            offset = _find_first_magic_signature(input_file, signature_lower)

        if offset is None:
            raise ValueError(f"无法找到指定格式({signature})的嵌入文件：{input_file}")

        size = os.path.getsize(input_file) - offset

    if size is None or size <= 0:
        raise ValueError(f"计算得到的提取大小({size})不合法，无法提取。")

    _copy_range_with_progress(input_file, output_file, offset, size, display_signature)


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
