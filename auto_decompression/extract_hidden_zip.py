import shutil
import sys
import struct
import os

# 可调整参数
STREAMING_SIZE_THRESHOLD = 3 * 1024 * 1024 * 1024  # 3GB 阈值
SEARCH_RATIO = 0.2  # 只在前 20% 的文件内容中搜索
CHUNK_SIZE = 256 * 1024 * 1024  # 256 MB

# 定义 RAR 和 ZIP 的签名
RAR_SIGNATURE = [b'Rar!\x1A\x07\x00', b'Rar!\x1A\x07\x01\x00']
ZIP_SIGNATURE = [b'PK\x03\x04']


def find_signature_in_file(filename, signatures):
    """
    在指定文件的前部数据中，以流式分块方式搜索任意指定签名，返回 (pos, signature)，
    找不到则返回 (None, None)。
    """
    file_size = os.path.getsize(filename)

    # 根据文件大小决定最多读取的字节数
    if file_size > STREAMING_SIZE_THRESHOLD:
        max_search_size = int(file_size * SEARCH_RATIO)
    else:
        max_search_size = file_size

    max_sig_len = max(len(sig) for sig in signatures)

    with open(filename, 'rb') as f:
        bytes_to_read = max_search_size
        total_read = 0  # 已经读取的字节数
        leftover = b""  # 用于处理跨块边界的残留数据

        while bytes_to_read > 0:
            # 本次需要读取的块大小
            chunk_size = min(CHUNK_SIZE, bytes_to_read)
            chunk = f.read(chunk_size)
            if not chunk:
                break  # 文件读完

            # 拼接 leftover 和当前 chunk
            combined = leftover + chunk

            # 在 combined 中查找签名
            for sig in signatures:
                pos = combined.find(sig)
                if pos != -1:
                    # 找到后，计算在整个文件中的绝对位置
                    absolute_pos = total_read - len(leftover) + pos
                    return absolute_pos, sig

            # 如果没找到，则将 combined 的后 (max_sig_len - 1) 字节留下来
            # 防止签名跨 chunk 边界
            if len(combined) >= max_sig_len - 1:
                leftover = combined[-(max_sig_len - 1):]
            else:
                leftover = combined

            # 累计读入的字节数
            bytes_read_this_round = len(chunk)
            total_read += bytes_read_this_round
            bytes_to_read -= bytes_read_this_round

    # 未找到签名
    return None, None


def read_vint(file_obj):
    """
    读取可变长度整数（vint）。
    参考 RAR 文档中对 Header Size、Header Type 等字段的可变长度编码描述。
    """
    result = 0
    shift = 0
    while True:
        byte = file_obj.read(1)
        if not byte:
            raise EOFError("Unexpected end of file while reading vint.")
        byte_val = byte[0]  # 等价于 ord(byte) ，更符合 Python3 习惯
        result |= (byte_val & 0x7F) << shift
        if not (byte_val & 0x80):
            break
        shift += 7
    return result


def has_embedded_signature(filename, signature):
    """
    检查 filename 文件内是否包含指定(或指定列表) signature 并进行初步有效性验证，
    若有效，则返回 True，否则返回 False。
    """
    # 如果传入的 signature 非列表，则转换成列表处理
    if not isinstance(signature, list):
        signature = [signature]

    # 查找签名位置
    pos, matched_signature = find_signature_in_file(filename, signature)
    if pos is None:
        return False

    # 进一步验证文件头是否真的有效
    with open(filename, 'rb') as f:
        f.seek(pos + len(matched_signature))

        # 如果匹配到的是 ZIP 签名
        if matched_signature in ZIP_SIGNATURE:
            # 读取 ZIP 本地文件头的额外 30 个字节进行验证
            header_data = f.read(30)
            if len(header_data) < 30:
                return False

            try:
                # 解包字段：版本、通用位标记、压缩方法等
                version, flag, method, mod_time, mod_date, crc32, comp_size, \
                    uncomp_size, filename_len, extra_len = struct.unpack('<HHHHHIIIHH', header_data[:26])
                
                filename_inner = f.read(filename_len)
                extra_field = f.read(extra_len)

                # 严格验证：检查文件名长度、扩展字段长度和压缩方法
                if (
                    filename_len >= 0 and extra_len >= 0
                    and method in (0, 8, 12, 14)  # 有效压缩方法
                    and len(filename_inner) == filename_len
                    and len(extra_field) == extra_len
                ):
                    return True
            except struct.error:
                return False

        # 如果匹配到的是 RAR 签名
        elif matched_signature in RAR_SIGNATURE:
            # 验证 RAR marker block 和 archive header
            try:
                # 读取 Header CRC32
                header_crc32 = f.read(4)
                if len(header_crc32) < 4:
                    return False

                # 读取 Header size (vint)
                header_size = read_vint(f)
                # 读取 Header type (vint)
                header_type = read_vint(f)

                # 检查 Header type 是否是有效类型
                if header_type not in (1, 2, 3, 4, 5):
                    return False

                # 如果通过检查，认为这是一个有效的 RAR 文件
                return True
            except (EOFError, struct.error):
                return False

    return False


def extract_embedded_file(input_file, output_file, signature):
    """
    从 input_file 中找到 signature 对应的起始位置，一直读到文件尾，将数据写入 output_file。
    采用 shutil.copyfileobj 实现分块拷贝，避免高内存占用。
    """
    start_pos, sig = find_signature_in_file(input_file, signature)
    if start_pos is None:
        raise ValueError(f"无法找到指定文件的起始位置：{input_file}")

    with open(input_file, 'rb') as f_in, open(output_file, 'wb') as f_out:
        # 移动到签名起始位置
        f_in.seek(start_pos)
        # 使用 copyfileobj 的 length 参数控制单次拷贝的块大小，防止占用过多内存
        shutil.copyfileobj(f_in, f_out, length=CHUNK_SIZE)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使用方法: python split_combined_file.py <文件1> <文件2> ...")
        sys.exit(1)

    for input_file in sys.argv[1:]:
        try:
            # 先检测 ZIP
            if has_embedded_signature(input_file, ZIP_SIGNATURE):
                output_zip = f"{input_file}_embedded.zip"
                extract_embedded_file(input_file, output_zip, ZIP_SIGNATURE)
                print(f"成功提取 ZIP 文件: {output_zip}")
            else:
                # 再检测 RAR
                for rar_sig in RAR_SIGNATURE:
                    if has_embedded_signature(input_file, rar_sig):
                        output_rar = f"{input_file}_embedded.rar"
                        extract_embedded_file(input_file, output_rar, rar_sig)
                        print(f"成功提取 RAR 文件: {output_rar}")
                        break
                else:
                    print(f"文件 {input_file} 中未发现嵌入的 ZIP 或 RAR 文件。")
        except Exception as e:
            print(f"处理文件 {input_file} 时出错: {e}")
