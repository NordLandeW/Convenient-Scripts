# Fix-name

这个脚本通过启发式规则和常见字字典处理给定目录下的乱码文件/文件夹名，并优先给出最可能的修复方式。

也可以指定当前编码与实际编码直接对所有文件/文件名进行修复。

脚本主要针对 CJK 字符工作。

需要安装依赖使脚本正常工作。

```bash
pip install -r requirements.txt
```

额外地，可以使用 `minimize_dict.py` 来最小化字典。

## 致谢
常用字字典使用了下面列出的项目：
[Kanji usage frequency](https://scriptin.github.io/kanji-frequency/)
[nk2028/commonly-used-chinese-characters-and-words](https://github.com/nk2028/commonly-used-chinese-characters-and-words)