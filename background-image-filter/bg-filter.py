#!/usr/bin/env python3
import os
import sys
import math
import argparse
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk
import subprocess
from concurrent.futures import ThreadPoolExecutor
import struct
import ctypes
from ctypes import c_void_p, c_size_t, memmove, windll
from loguru import logger

import math
import os

def compute_score(img_path, screen_ratio, max_area, max_size):
    info = get_image_info(img_path)
    if info is None:
        return -10000
    width, height, ratio, file_size = info

    # 1. 比例评分（最高100分）
    score_aspect = aspect_ratio_score(ratio, screen_ratio)

    # 2. 格式评分（保持原有逻辑）
    ext = os.path.splitext(img_path)[1].lower()
    if ext == '.png':
        score_format = 20
    elif ext in ['.jpg', '.jpeg']:
        score_format = 10
    elif ext == '.gif':
        score_format = -1000
    else:
        score_format = 0

    # 3. 分辨率评分（最高60分）
    if width * height >= 3840 * 2160:  # 4K
        score_resolution = 60
    elif width * height >= 2560 * 1440:  # 2K
        score_resolution = 40
    elif width * height >= 1920 * 1080:  # 1K
        score_resolution = 20
    elif width * height >= 1280 * 720:
        score_resolution = 10
    else:
        score_resolution = -40

    # 4. 文件大小评分（最高30分）
    score_filesize = 30 * (file_size / max_size) if max_size > 0 else 0
    
    return score_aspect + score_format + score_resolution + score_filesize

def aspect_ratio_score(image_ratio, desired_ratio):
    """
    使用倍数偏离度 + 高斯衰减计算纵横比评分，返回 [0, 100] 区间的分数。
    """
    # 如果只接受宽屏，可在这里加一条限制
    # if image_ratio < 1:
    #    return 0  # 或者只给个极低的上限，比如 return 10

    # 倍数偏离度: ratio_factor >= 1
    # ratio_factor = 1 表示与期望值相同
    # ratio_factor = 2 表示宽（或窄）了一倍
    ratio_factor = max(image_ratio / desired_ratio, desired_ratio / image_ratio)
    
    # 若 ratio_factor=1 则 perfect；越大(或越小)表示偏离越大
    # 这里选择高斯衰减：score = 100 * exp( -alpha * ( ln(rf) )^2 )
    alpha = 2.0          # 惩罚因子，可调大一点，如果希望对偏离更敏感
    max_score = 100.0    # 纵横比评分的满分
    # 取 ln(ratio_factor)，无论是 >1 还是 <1，平方后一样对待
    diff = math.log(ratio_factor)
    # 高斯衰减
    raw_score = max_score * math.exp(-alpha * (diff ** 2))

    # 也可以再根据是否是大于1还是小于1做一个额外的微调（可选）
    return raw_score

def get_image_info(filepath):
    """
    获取图片信息：宽度、高度、宽高比和文件大小
    """
    try:
        with Image.open(filepath) as img:
            width, height = img.size
            ratio = width / height if height != 0 else 0
    except Exception as e:
        return None
    file_size = os.path.getsize(filepath)
    return width, height, ratio, file_size

def sort_key(filepath, screen_ratio):
    """
    构造排序 key：
      - 与屏幕宽高比差值（越小越好）
      - png 图片优先（png 得到 0，否则 1）
      - 分辨率（宽×高，越大越好）
      - 文件大小（作为码率代理，越大越好）
    """
    info = get_image_info(filepath)
    if info is None:
        return (float('inf'), 1, 0, 0)
    width, height, ratio, file_size = info
    diff = abs(ratio - screen_ratio)
    ext = os.path.splitext(filepath)[1].lower()
    is_png = 0 if ext == '.png' else 1
    resolution = width * height
    return (diff, is_png, -resolution, -file_size)

def collect_images(folder):
    """
    遍历文件夹及其子文件夹，收集常见图片的路径
    """
    image_files = []
    allowed_ext = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff'}
    for root, dirs, files in os.walk(folder):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in allowed_ext:
                image_files.append(os.path.join(root, f))
    return image_files

# 将文件作为单个文件复制到剪贴板（仅在 Windows 上支持）
def copy_file_to_clipboard(filepath):
    try:
        import win32clipboard
        import win32con
    except ImportError:
        print("复制文件到剪贴板需要 pywin32 模块。")
        return
    try:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        # 构造 DROPFILES 结构体
        # DROPFILES { DWORD pFiles, POINT pt, BOOL fNC, BOOL fWide }
        dropfiles_header = struct.pack("IiiII", 20, 0, 0, 0, 1)
        file_list = (filepath + "\0").encode("utf-16le") + b"\0\0"
        data = dropfiles_header + file_list

        GHND = 0x0042  # GMEM_MOVEABLE | GMEM_ZEROINIT

        # 设置 GlobalAlloc/GlobalLock 参数类型与返回值类型
        windll.kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_ulong]
        windll.kernel32.GlobalAlloc.restype = c_void_p
        windll.kernel32.GlobalLock.argtypes = [c_void_p]
        windll.kernel32.GlobalLock.restype = c_void_p

        hGlobalMem = windll.kernel32.GlobalAlloc(GHND, len(data))
        if not hGlobalMem:
            print("GlobalAlloc failed")
            win32clipboard.CloseClipboard()
            return
        pGlobalMem = windll.kernel32.GlobalLock(hGlobalMem)
        if not pGlobalMem:
            print("GlobalLock failed")
            win32clipboard.CloseClipboard()
            return
        memmove(pGlobalMem, data, len(data))
        windll.kernel32.GlobalUnlock(ctypes.c_void_p(hGlobalMem))
        win32clipboard.SetClipboardData(win32con.CF_HDROP, hGlobalMem)
        win32clipboard.CloseClipboard()
        print("文件已复制到剪贴板:", filepath)
    except Exception as e:
        logger.exception("复制文件到剪贴板失败")
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass

def open_external_and_copy(img_path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(img_path)
            copy_file_to_clipboard(img_path)
        elif sys.platform.startswith("darwin"):
            subprocess.call(["open", img_path])
            print("复制功能仅在 Windows 上支持。")
        else:
            subprocess.call(["xdg-open", img_path])
            print("复制功能仅在 Windows 上支持。")
    except Exception as e:
        print("打开外部程序失败:", e)

class ImageBrowser:
    def __init__(self, master, folder=None, page_size=12):
        self.master = master
        self.master.title("图片浏览器")
        self.page_size = page_size
        self.folder = folder
        self.images = []
        self.sorted_images = []
        self.thumbnails = {}  # 缓存：key 为 (img_path, target_size)
        self.thumbnail_futures = {}  # 后台任务缓存
        self.current_page = 0
        self.current_columns = 3  # 固定每页图片数排布

        self.placeholder_image = ImageTk.PhotoImage(Image.new("RGB", (10, 10), "gray"))
        self.executor = ThreadPoolExecutor(max_workers=4)

        self.setup_ui()

        # 主界面左右方向键快捷翻页
        self.master.bind("<Left>", lambda event: self.prev_page())
        self.master.bind("<Right>", lambda event: self.next_page())

        if self.folder:
            self.load_folder(self.folder)

    def setup_ui(self):
        top_frame = tk.Frame(self.master)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        self.folder_entry = tk.Entry(top_frame, width=50)
        self.folder_entry.pack(side=tk.LEFT, padx=5)
        browse_button = tk.Button(top_frame, text="选择文件夹", command=self.browse_folder)
        browse_button.pack(side=tk.LEFT, padx=5)
        load_button = tk.Button(top_frame, text="加载图片", command=lambda: self.load_folder(self.folder_entry.get()))
        load_button.pack(side=tk.LEFT, padx=5)
        prev_button = tk.Button(top_frame, text="上一页", command=self.prev_page)
        prev_button.pack(side=tk.LEFT, padx=5)
        self.page_label = tk.Label(top_frame, text="Page 0/0")
        self.page_label.pack(side=tk.LEFT, padx=5)
        next_button = tk.Button(top_frame, text="下一页", command=self.next_page)
        next_button.pack(side=tk.LEFT, padx=5)

        self.middle_frame = tk.Frame(self.master)
        self.middle_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.middle_frame.bind("<Configure>", lambda event: self.display_page())

    def browse_folder(self):
        folder = filedialog.askdirectory(title="选择图片文件夹")
        if folder:
            self.folder_entry.delete(0, tk.END)
            self.folder_entry.insert(0, folder)
            self.load_folder(folder)

    def load_folder(self, folder):
        self.images = collect_images(folder)
        if not self.images:
            messagebox.showinfo("提示", "该文件夹下没有找到图片！")
            return
        # 计算屏幕比例及所有图片的最大分辨率和文件大小
        screen_width = self.master.winfo_screenwidth()
        screen_height = self.master.winfo_screenheight()
        self.screen_ratio = screen_width / screen_height
        max_area = 0
        max_size = 0
        for path in self.images:
            info = get_image_info(path)
            if info is not None:
                width, height, ratio, file_size = info
                area = width * height
                if area > max_area:
                    max_area = area
                if file_size > max_size:
                    max_size = file_size
        self.max_area = max_area
        self.max_size = max_size
        # 按启发式评分排序（分数越高越好）
        self.sorted_images = sorted(self.images, key=lambda x: compute_score(x, self.screen_ratio, self.max_area, self.max_size), reverse=True)


    def display_page(self):
        for widget in self.middle_frame.winfo_children():
            widget.destroy()
        start_index = self.current_page * self.page_size
        end_index = start_index + self.page_size
        page_images = self.sorted_images[start_index:end_index]

        columns = self.current_columns
        rows = math.ceil(len(page_images) / columns)
        frame_width = self.middle_frame.winfo_width()
        frame_height = self.middle_frame.winfo_height()
        if frame_width <= 0 or frame_height <= 0:
            cell_width, cell_height = 150, 150
        else:
            pad = 5
            cell_width = (frame_width - (columns+1)*pad) / columns
            cell_height = (frame_height - (rows+1)*pad) / rows

        target_size = (int(cell_width), int(cell_height))
        pad = 2
        for idx, img_path in enumerate(page_images):
            row = idx // columns
            col = idx % columns
            thumb = self.get_thumbnail(img_path, target_size)
            if thumb is None:
                thumb = self.placeholder_image
            cur_index = start_index + idx
            # 用 Canvas 显示缩略图和评分
            cell_canvas = tk.Canvas(self.middle_frame, width=target_size[0], height=target_size[1], highlightthickness=0)
            cell_canvas.create_image(target_size[0]//2, target_size[1]//2, image=thumb)
            score = compute_score(img_path, self.screen_ratio, self.max_area, self.max_size)
            # 数字向内偏移4像素，避免被右侧裁剪
            cell_canvas.create_text(target_size[0]-4, target_size[1]-4, text=str(int(score)), anchor="se", fill="green", font=("Arial", 10, "bold"))
            cell_canvas.grid(row=row, column=col, padx=pad, pady=pad, sticky="nsew")
            cell_canvas.bind("<Button-1>", lambda event, path=img_path, idx=cur_index: self.open_preview(path, idx))
            cell_canvas.bind("<Button-3>", lambda event, path=img_path: open_external_and_copy(path))
            self.middle_frame.grid_columnconfigure(col, weight=1)



        total_pages = (len(self.sorted_images) + self.page_size - 1) // self.page_size
        self.page_label.config(text=f"Page {self.current_page+1}/{total_pages}")

    def get_thumbnail(self, img_path, target_size):
        key = (img_path, target_size)
        if key in self.thumbnails:
            return self.thumbnails[key]
        if key not in self.thumbnail_futures:
            future = self.executor.submit(self.generate_thumbnail_image, img_path, target_size)
            self.thumbnail_futures[key] = future
            future.add_done_callback(lambda fut, key=key: self.master.after(0, self.thumbnail_done_callback, key, fut))
        return self.placeholder_image

    def generate_thumbnail_image(self, img_path, target_size):
        try:
            with Image.open(img_path) as img:
                img_copy = img.copy()
            img_copy.thumbnail(target_size, Image.Resampling.HAMMING)
            return img_copy
        except Exception as e:
            print(f"生成缩略图 {img_path} 失败: {e}")
            return None

    def thumbnail_done_callback(self, key, fut):
        pil_image = fut.result()
        if pil_image is not None:
            try:
                photo = ImageTk.PhotoImage(pil_image)
                self.thumbnails[key] = photo
            except Exception as e:
                print("转换 PhotoImage 失败:", e)
        self.display_page()

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.display_page()

    def next_page(self):
        total_pages = (len(self.sorted_images) + self.page_size - 1) // self.page_size
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self.display_page()

    def open_preview(self, img_path, index):
        PreviewWindow(self.master, self.sorted_images, index, self.screen_ratio, self.max_area, self.max_size)


class PreviewWindow:
    def __init__(self, master, image_list, index, screen_ratio, max_area, max_size):
        self.screen_ratio = screen_ratio
        self.max_area = max_area
        self.max_size = max_size

        self.master = master
        self.image_list = image_list
        self.index = index
        self.img_path = self.image_list[self.index]
        self.top = tk.Toplevel(master)
        self.top.title(f"预览: {os.path.basename(self.img_path)}")
        self.top.focus_force()
        self.top.lift()
        sw = master.winfo_screenwidth()
        sh = master.winfo_screenheight()
        w = int(sw * 0.75)
        h = int(sh * 0.75)
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.top.geometry(f"{w}x{h}+{x}+{y}")

        try:
            self.original_image = Image.open(self.img_path)
        except Exception as e:
            print(f"无法打开图片: {self.img_path}\n{e}")
            self.top.destroy()
            return

        self.zoom_level = 1.0

        self.canvas = tk.Canvas(self.top, bg="gray")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda event: self.display_image())

        self.slider = tk.Scale(self.top, from_=0.1, to=3.0, resolution=0.1,
                               orient=tk.HORIZONTAL, label="额外缩放", command=self.update_zoom)
        self.slider.set(1.0)
        self.slider.pack(fill=tk.X)
        self.info_label = tk.Label(self.top, text="", anchor="w")
        self.info_label.pack(fill=tk.X, padx=5, pady=5)

        btn_frame = tk.Frame(self.top)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        prev_btn = tk.Button(btn_frame, text="上一张", command=self.prev_image)
        prev_btn.pack(side=tk.LEFT, padx=10, pady=5)
        next_btn = tk.Button(btn_frame, text="下一张", command=self.next_image)
        next_btn.pack(side=tk.LEFT, padx=10, pady=5)
        ext_btn = tk.Button(btn_frame, text="外部打开并复制", command=self.open_external_and_copy)
        ext_btn.pack(side=tk.RIGHT, padx=10, pady=5)


        self.top.bind("<Left>", lambda event: self.prev_image())
        self.top.bind("<Right>", lambda event: self.next_image())
        self.top.bind("<Control-c>", lambda event: self.copy_current_file())

        self.display_image()

    def display_image(self):
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        if canvas_width <= 1 or canvas_height <= 1:
            return
        try:
            orig_width, orig_height = self.original_image.size
        except Exception as e:
            print("获取图片尺寸失败:", e)
            return
        base_zoom = min(canvas_width / orig_width, canvas_height / orig_height)
        effective_zoom = base_zoom * self.zoom_level
        new_size = (int(orig_width * effective_zoom), int(orig_height * effective_zoom))
        try:
            resized = self.original_image.resize(new_size, Image.Resampling.HAMMING)
            self.photo = ImageTk.PhotoImage(resized)
        except Exception as e:
            print("图片缩放失败:", e)
            return
        self.canvas.delete("all")
        self.canvas.create_image(canvas_width/2, canvas_height/2, anchor=tk.CENTER, image=self.photo)
        self.canvas.config(scrollregion=self.canvas.bbox(tk.ALL))
        self.top.title(f"预览: {os.path.basename(self.img_path)}")
        # 更新预览信息：尺寸、文件大小和评分
        info = get_image_info(self.img_path)
        if info is not None:
            width, height, ratio, file_size = info
            score = compute_score(self.img_path, self.screen_ratio, self.max_area, self.max_size)
            info_text = f"尺寸: {width}x{height}, 大小: {file_size} bytes, 分数: {score:.1f}"
            self.info_label.config(text=info_text)

    def update_zoom(self, val):
        try:
            self.zoom_level = float(val)
            self.display_image()
        except Exception as e:
            print("更新缩放出错:", e)

    def prev_image(self):
        if self.index > 0:
            self.index -= 1
            self.load_image()

    def next_image(self):
        if self.index < len(self.image_list) - 1:
            self.index += 1
            self.load_image()

    def load_image(self):
        self.img_path = self.image_list[self.index]
        try:
            self.original_image = Image.open(self.img_path)
        except Exception as e:
            print(f"无法打开图片: {self.img_path}\n{e}")
            return
        self.slider.set(1.0)
        self.zoom_level = 1.0
        self.display_image()

    def open_external_and_copy(self):
        open_external_and_copy(self.img_path)

    def copy_current_file(self):
        copy_file_to_clipboard(self.img_path)

def main():
    parser = argparse.ArgumentParser(description="图片浏览器 GUI")
    parser.add_argument("--folder", help="要扫描的文件夹路径")
    parser.add_argument("--page-size", type=int, default=12, help="每页显示图片数量，默认12个")
    args = parser.parse_args()

    root = tk.Tk()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    w = int(sw * 0.75)
    h = int(sh * 0.75)
    x = (sw - w) // 2
    y = (sh - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    app = ImageBrowser(root, folder=args.folder, page_size=args.page_size)
    root.mainloop()

if __name__ == "__main__":
    main()
