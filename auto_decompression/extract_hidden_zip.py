import os
import sys
import re
import zipfile

def extract_zip_from_combined_file(input_file, output_file):
    with open(input_file, 'rb') as f:
        data = f.read()

    # 使用正则表达式查找 ZIP 文件的签名 (0x504b0304)
    zip_start = re.search(b'PK\x03\x04', data)
    if not zip_start:
        raise ValueError(f"无法找到 ZIP 文件的起始位置：{input_file}")

    zip_start_pos = zip_start.start()

    # 将 ZIP 部分写入新的文件
    with open(output_file, 'wb') as zip_file:
        zip_file.write(data[zip_start_pos:])
    
    # print(f"成功提取 ZIP 文件: {output_file}")

def has_embedded_zip(input_file):
    with open(input_file, 'rb') as f:
        data = f.read()

    # 使用正则表达式查找 ZIP 文件的签名 (0x504b0304)
    zip_start = re.search(b'PK\x03\x04', data)
    if not zip_start:
        return False

    zip_start_pos = zip_start.start()
    zip_data = data[zip_start_pos:]

    # 检查嵌入 ZIP 的大小是否与文件大小相同，以判断是否为嵌入的 ZIP
    try:
        with open(input_file, 'rb') as f:
            f.seek(zip_start_pos)
            embedded_zip_size = f.tell() - zip_start_pos
            return embedded_zip_size < len(data) - zip_start_pos
    except zipfile.BadZipFile:
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使用方法: python split_combined_file.py <文件1> <文件2> ...")
        sys.exit(1)

    # 处理所有传入的文件
    for input_file in sys.argv[1:]:
        try:
            if has_embedded_zip(input_file):
                output_zip = f"{input_file}_embedded.zip"
                extract_zip_from_combined_file(input_file, output_zip)
            else:
                print(f"文件 {input_file} 中未发现嵌入的 ZIP 文件。")
        except Exception as e:
            print(f"处理文件 {input_file} 时出错: {e}")
