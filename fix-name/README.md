# Fix-name

这个脚本通过启发式规则和常见字字典处理给定目录下的乱码文件/文件夹名，并优先给出最可能的修复方式。

也可以指定当前编码与实际编码直接对所有文件/文件名进行修复。

脚本主要针对 CJK 字符工作。当前默认只进行 dry-run；确认预览无误后追加 `--apply` 才会真正改名。

## 当前策略

- 默认 dry-run，只有 `--apply` 才会真正改名。
- 文件与目录可分开处理：`--target files|dirs|both`。
- 默认只转换文件 stem，保留扩展名，避免 `.png` / `.zip` 等扩展名被误改。
- 每个名称独立选择最优编码链，支持单层与多层乱码链，例如 `gbk->cp932`、`gbk->utf-8->gbk->cp932`。
- 使用 strict 编码转换；转换失败的候选会被丢弃，不用 `replace` 静默吞错。
- 评分会惩罚替换字符、Windows 非法字符、PUA、连续半角片假名和常见 mojibake 片段。
- 会利用父目录/祖先目录作为上下文：如果转换后的短文件名前缀能匹配所在目录名，可提升置信度；这用于泛化处理短角色名，避免仅靠硬编码样本。
- 生成 JSONL 改名计划与 apply/undo 日志，便于审查和撤销。


常用示例：

```bash
# 只预览文件名修复，目录名不动
python fixname.py --dir "D:\path" --target files

# 典型日文 Shift-JIS/CP932 文件名被 GBK 误解的情况
python fixname.py --dir "D:\path" --target files --current-enc gbk --actual-enc cp932

# 确认 dry-run 输出无误后再执行
python fixname.py --dir "D:\path" --target files --current-enc gbk --actual-enc cp932 --apply

# 如需撤销，使用 dry-run 时输出的 JSONL 计划
python fixname.py --undo fixname-undo-YYYYMMDD-HHMMSS.jsonl --apply
```

需要安装依赖使脚本正常工作。

```bash
pip install -r requirements.txt
```

额外地，可以使用 `minimize_dict.py` 来最小化字典。

## 致谢
常用字字典使用了下面列出的项目：

[Kanji usage frequency](https://scriptin.github.io/kanji-frequency/)

[nk2028/commonly-used-chinese-characters-and-words](https://github.com/nk2028/commonly-used-chinese-characters-and-words)
