import sys

RAR_SIGNATURE = b'Rar!\x1A\x07'
ZIP_SIGNATURE = b'PK\x03\x04'

def find_signature_in_file(filename, signature):
    with open(filename, 'rb') as f:
        data = f.read()
        pos = data.find(signature)
        return pos if pos != -1 else None

def has_embedded_signature(filename, signature):
    return find_signature_in_file(filename, signature) is not None

def extract_embedded_file(input_file, output_file, signature):
    with open(input_file, 'rb') as f:
        data = f.read()
        start_pos = data.find(signature)
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
            elif has_embedded_signature(input_file, RAR_SIGNATURE):
                output_rar = f"{input_file}_embedded.rar"
                extract_embedded_file(input_file, output_rar, RAR_SIGNATURE)
                print(f"成功提取 RAR 文件: {output_rar}")
            else:
                print(f"文件 {input_file} 中未发现嵌入的 ZIP 或 RAR 文件。")
        except Exception as e:
            print(f"处理文件 {input_file} 时出错: {e}")
