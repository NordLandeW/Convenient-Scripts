import sys
import struct

# 定义 RAR 和 ZIP 的签名
RAR_SIGNATURE = [b'Rar!\x1A\x07\x00', b'Rar!\x1A\x07\x01\x00']
ZIP_SIGNATURE = [b'PK\x03\x04']

def find_signature_in_file(filename, signatures):
    with open(filename, 'rb') as f:
        data = f.read()
        for signature in signatures:
            pos = data.find(signature)
            if pos != -1:
                return pos, signature
    return None, None

def read_vint(file):
    """读取可变长度整数（vint）"""
    result = 0
    shift = 0
    while True:
        byte = file.read(1)
        if not byte:
            raise EOFError("Unexpected end of file while reading vint.")
        byte = ord(byte)
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result

def has_embedded_signature(filename, signature):
    if type(signature) != type([]):
        signature = [signature]
    pos, matched_signature = find_signature_in_file(filename, signature)
    if pos is None:
        return False
    with open(filename, 'rb') as f:
        f.seek(pos+len(matched_signature))
        if matched_signature in ZIP_SIGNATURE:
            # 读取 ZIP 本地文件头的额外 30 个字节进行验证
            header_data = f.read(30)
            if len(header_data) < 30:
                return False
            
            # 解包字段：版本、通用位标记、压缩方法等
            try:
                version, flag, method, mod_time, mod_date, crc32, comp_size, uncomp_size, filename_len, extra_len = struct.unpack('<HHHHHIIIHH', header_data[:26])
                filename = f.read(filename_len)
                extra_field = f.read(extra_len)
                # 严格验证：检查文件名长度、扩展字段长度是否合理，以及压缩方法是否有效
                if (filename_len >= 0 and extra_len >= 0 and 
                    method in (0, 8, 12, 14) and  # 有效的压缩方法：0 - 储存，8 - deflate 等
                    len(filename) == filename_len and len(extra_field) == extra_len):
                    return True
            except struct.error:
                return False
        elif matched_signature in RAR_SIGNATURE:
            # For RAR, validate the marker block and archive header
            # RAR marker block: https://www.rarlab.com/technote.htm
            try:
                # Read Header CRC32
                header_crc32 = f.read(4)
                if len(header_crc32) < 4:
                    return False
                
                # Read Header size (vint)
                header_size = read_vint(f)
                
                # Read Header type (vint)
                header_type = read_vint(f)
                
                # Check if Header type is valid for RAR archive
                if header_type not in (1, 2, 3, 4, 5):  # Valid header types for RAR archives
                    return False
                
                # If all checks pass, assume it's a valid RAR file
                return True
            except (EOFError, struct.error):
                return False
    return False

def extract_embedded_file(input_file, output_file, signature):
    with open(input_file, 'rb') as f:
        data = f.read()
        start_pos, sig = find_signature_in_file(input_file, signature)
        if start_pos == -1:
            raise ValueError(f"无法找到指定文件的起始位置：{input_file}")

        # 从签名开始位置复制数据到输出文件
        with open(output_file, 'wb') as out_file:
            out_file.write(data[start_pos:])

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使用方法: python split_combined_file.py <文件1> <文件2> ...")
        sys.exit(1)

    for input_file in sys.argv[1:]:
        try:
            if has_embedded_signature(input_file, ZIP_SIGNATURE):
                output_zip = f"{input_file}_embedded.zip"
                extract_embedded_file(input_file, output_zip, ZIP_SIGNATURE)
                print(f"成功提取 ZIP 文件: {output_zip}")
            else:
                for signature in RAR_SIGNATURE:
                    if has_embedded_signature(input_file, signature):
                        output_rar = f"{input_file}_embedded.rar"
                        extract_embedded_file(input_file, output_rar, signature)
                        print(f"成功提取 RAR 文件: {output_rar}")
                        break
                else:
                    print(f"文件 {input_file} 中未发现嵌入的 ZIP 或 RAR 文件。")
        except Exception as e:
            print(f"处理文件 {input_file} 时出错: {e}")