# Image Unfolder Script

一个用于将文件从子目录展平（移动）到根目录，并在之后恢复它们的小工具。
支持 Windows / Linux 路径。

**安装**
```bash
pip install -r requirements.txt
```

**展平文件**
```bash
# 展平当前目录
python unfolder.py rename

# 展平指定目录并保留 1 级父目录
python unfolder.py rename --dir "/path/to/folder" --floor 1
```

**恢复文件**
```bash
# 恢复文件 (默认会删除 .rename_lib)
python unfolder.py repack --dir "/path/to/folder"

# 恢复文件并保留 .rename_lib
python unfolder.py repack --keep-lib
```
