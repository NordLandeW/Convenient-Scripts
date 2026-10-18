# Background-image-filter

以启发式评分在指定文件夹中选出最适合当成壁纸的图片。

## 运行

```bash
python bg-filter.py --folder "D:\Pictures" --page-size 12 --ratio 16:9
```

- `--folder` 要扫描的文件夹（可稍后在界面里选择）
- `--page-size` 每页缩略图数量，默认 12
- `--ratio` 目标纵横比，例如 `16:9`；默认取屏幕比例

## Qt 版本

`bg-filter.py` 是 PySide6 (Qt 6) 实现。旧的 Tk 实现保留为 `bg-filter-tk.py`，功能相同，便于对照或回退。界面使用 Qt 原生的 Windows 11 (WinUI) 控件样式，中文优先 Microsoft YaHei UI、英文回退 Segoe UI；其他平台自动回退到 Fusion。

渲染路径：

- 缩略图网格和预览画布在可用时使用 `QOpenGLWidget`，由 GPU 完成合成与交互缩放的临时插值。
- 最终高质量缩放仍由 Pillow 的 LANCZOS 在后台线程完成；GPU 不参与图像重采样。
- 设置 `BG_FILTER_RENDER=raster` 可强制软件光栅渲染；没有可用 OpenGL 上下文时会自动回退。

## 操作

- 单击缩略图打开预览；右键在外部程序中打开并复制文件。
- `←` / `→` 翻页或切换图片；预览中滚轮缩放、拖动平移。
- “比例遮挡” 按目标纵横比压暗缩略图和预览中不可见的部分。
- “跳至” 支持页码钳制；`重新计算` 会按当前比例重新扫描、评分和排序。

## 依赖

```bash
pip install -r requirements.txt
```

Windows 剪贴板复制需要 `pywin32`；Linux 需要 `wl-copy`、`xclip` 或 `xsel` 之一。
