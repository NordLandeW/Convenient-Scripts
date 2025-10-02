#!/usr/bin/env python3
import os
import sys
import math
import argparse
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
import struct
import ctypes
from ctypes import c_void_p, c_size_t, memmove, windll
from loguru import logger

def _print_progress(cur, total, prefix=""):
    pct = cur * 100 // total
    sys.stdout.write(f"\r{prefix}{cur}/{total}  {pct}%")
    sys.stdout.flush()

def compute_score(img_path, screen_ratio, max_area, max_size):
    info = get_image_info(img_path)
    if info is None:
        return -10000
    width, height, ratio, file_size = info

    # 1. 比例评分
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

    # 3. 分辨率评分（最高45分）
    if width * height >= 3840 * 2160:  # 4K
        score_resolution = 45
    elif width * height >= 2560 * 1440:  # 2K
        score_resolution = 40
    elif width * height >= 1920 * 1080:  # 1K
        score_resolution = 20
    elif width * height >= 1280 * 720:
        score_resolution = 10
    else:
        score_resolution = -40

    # 4. 文件大小评分（最高20分）
    score_filesize = 20 * (file_size / max_size) if max_size > 0 else 0

    return score_aspect + score_format + score_resolution + score_filesize


def aspect_ratio_score(image_ratio, desired_ratio):
    """
    计算比例评分：以覆盖方式放置壁纸时，计算无法显示的部分占比
    X% 的部分无法显示，X越大扣分越多
    """
    if desired_ratio <= 0 or image_ratio <= 0:
        return 0
    
    scale_factor = max(1.0 / image_ratio, desired_ratio)
    
    if image_ratio > desired_ratio:
        visible_ratio = desired_ratio / image_ratio
    else:
        visible_ratio = image_ratio / desired_ratio
    
    invisible_ratio = 1.0 - visible_ratio
    
    max_score = 200.0
    score = max_score * (1.0 - invisible_ratio)
    
    return score


def get_image_info(filepath):
    try:
        with Image.open(filepath) as img:
            width, height = img.size
            ratio = width / height if height != 0 else 0
    except Exception:
        return None
    file_size = os.path.getsize(filepath)
    return width, height, ratio, file_size


def sort_key(filepath, screen_ratio):
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
        dropfiles_header = struct.pack("IiiII", 20, 0, 0, 0, 1)
        file_list = (filepath + "\0").encode("utf-16le") + b"\0\0"
        data = dropfiles_header + file_list
        GHND = 0x0042
        windll.kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_ulong]
        windll.kernel32.GlobalAlloc.restype = c_void_p
        windll.kernel32.GlobalLock.argtypes = [c_void_p]
        windll.kernel32.GlobalLock.restype = c_void_p
        hGlobalMem = windll.kernel32.GlobalAlloc(GHND, len(data))
        if not hGlobalMem:
            win32clipboard.CloseClipboard()
            return
        pGlobalMem = windll.kernel32.GlobalLock(hGlobalMem)
        if not pGlobalMem:
            win32clipboard.CloseClipboard()
            return
        memmove(pGlobalMem, data, len(data))
        windll.kernel32.GlobalUnlock(ctypes.c_void_p(hGlobalMem))
        win32clipboard.SetClipboardData(win32con.CF_HDROP, hGlobalMem)
        win32clipboard.CloseClipboard()
        print("文件已复制到剪贴板:", filepath)
    except Exception:
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
    def __init__(self, master, folder=None, page_size=12, desired_ratio=None):
        self.master = master
        self.master.title("图片浏览器")
        self.page_size = page_size
        self.folder = folder
        self.images = []
        self.sorted_images = []
        self.thumbnails = {}
        self.thumbnail_futures = {}
        self.current_page = 0
        self.current_columns = 3
        self.desired_ratio = desired_ratio
        self.placeholder_image = ImageTk.PhotoImage(Image.new("RGB", (10, 10), "gray"))
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.setup_ui()
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
        
        # 添加屏幕比例预览勾选框
        self.show_overlay_var = tk.BooleanVar(value=False)
        overlay_checkbox = tk.Checkbutton(top_frame, text="显示屏幕比例遮挡", 
                                         variable=self.show_overlay_var, 
                                         command=self.display_page)
        overlay_checkbox.pack(side=tk.LEFT, padx=10)
        
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
            self.sorted_images = []
            self.current_page = 0
            self.display_page()
            return

        total = len(self.images)
        screen_width = self.master.winfo_screenwidth()
        screen_height = self.master.winfo_screenheight()
        self.screen_ratio = screen_width / screen_height

        # 阶段一：读取图片信息并统计 max_area / max_size
        self.max_area = 0
        self.max_size = 0
        for idx, path in enumerate(self.images, 1):
            info = get_image_info(path)
            if info is not None:
                w, h, _, sz = info
                area = w * h
                if area > self.max_area:
                    self.max_area = area
                if sz > self.max_size:
                    self.max_size = sz
            _print_progress(idx, total, "读取信息 ")

        print()  # 换行

        # 阶段二：多线程计算评分
        score_dict = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(compute_score, p, self.screen_ratio,
                                self.max_area, self.max_size): p for p in self.images}
            for idx, fut in enumerate(as_completed(futures), 1):
                p = futures[fut]
                score_dict[p] = fut.result()
                _print_progress(idx, total, "计算评分 ")
        print()  # 换行

        # 根据评分排序
        self.sorted_images = sorted(self.images, key=lambda x: score_dict[x], reverse=True)

    def display_page(self):
        for widget in self.middle_frame.winfo_children():
            widget.destroy()

        total_images = len(self.sorted_images)
        total_pages = (total_images + self.page_size - 1) // self.page_size if total_images else 0

        if total_pages == 0:
            self.page_label.config(text="Page 0/0")
            return

        if self.current_page >= total_pages:
            self.current_page = max(total_pages - 1, 0)

        start_index = self.current_page * self.page_size
        end_index = start_index + self.page_size
        page_images = self.sorted_images[start_index:end_index]

        if not page_images:
            self.page_label.config(text=f"Page {self.current_page + 1}/{total_pages}")
            return

        columns = self.current_columns
        rows = max(1, math.ceil(len(page_images) / columns))
        frame_width = self.middle_frame.winfo_width()
        frame_height = self.middle_frame.winfo_height()
        outer_pad = 5
        if frame_width <= 0 or frame_height <= 0:
            cell_width, cell_height = 150, 150
        else:
            cell_width = (frame_width - (columns + 1) * outer_pad) / columns
            cell_height = (frame_height - (rows + 1) * outer_pad) / rows
        target_size = (int(cell_width), int(cell_height))
        cell_pad = 2

        for idx, img_path in enumerate(page_images):
            row = idx // columns
            col = idx % columns
            thumb = self.get_thumbnail(img_path, target_size)
            if thumb is None:
                thumb = self.placeholder_image
            cur_index = start_index + idx
            cell_canvas = tk.Canvas(self.middle_frame, width=target_size[0], height=target_size[1], highlightthickness=0)
            cell_canvas.create_image(target_size[0] // 2, target_size[1] // 2, image=thumb)
            
            total_score = compute_score(img_path, self.screen_ratio, self.max_area, self.max_size)
            info = get_image_info(img_path)
            if info is not None:
                _, _, ratio, _ = info
                aspect_score = aspect_ratio_score(ratio, self.screen_ratio)
                score_text = f"{int(total_score)}\n({int(aspect_score)})"
            else:
                score_text = str(int(total_score))
            
            cell_canvas.create_text(target_size[0] - 4, target_size[1] - 4, text=score_text, anchor="se", fill="green", font=("Arial", 9, "bold"))
            cell_canvas.grid(row=row, column=col, padx=cell_pad, pady=cell_pad, sticky="nsew")
            cell_canvas.bind("<Button-1>", lambda event, path=img_path, idx=cur_index: self.open_preview(path, idx))
            cell_canvas.bind("<Button-3>", lambda event, path=img_path: open_external_and_copy(path))
            self.middle_frame.grid_columnconfigure(col, weight=1)
        
        if self.show_overlay_var.get():
            for idx, img_path in enumerate(page_images):
                row = idx // columns
                col = idx % columns
                cell_canvas = self.middle_frame.grid_slaves(row=row, column=col)[0]
                self.draw_thumbnail_overlay(cell_canvas, img_path, target_size)
        
        self.page_label.config(text=f"Page {self.current_page + 1}/{total_pages}")

    def draw_thumbnail_overlay(self, canvas, img_path, target_size):
        """在缩略图上绘制屏幕比例遮挡效果"""
        try:
            info = get_image_info(img_path)
            if info is None:
                return
            
            orig_width, orig_height, image_ratio, _ = info
            canvas_width, canvas_height = target_size
            
            # 计算缩略图的实际显示区域（因为 thumbnail 会保持比例）
            scale = min(canvas_width / orig_width, canvas_height / orig_height)
            thumb_width = orig_width * scale
            thumb_height = orig_height * scale
            
            # 缩略图在 canvas 中的位置（居中）
            thumb_x1 = (canvas_width - thumb_width) / 2
            thumb_y1 = (canvas_height - thumb_height) / 2
            thumb_x2 = thumb_x1 + thumb_width
            thumb_y2 = thumb_y1 + thumb_height
            
            # 计算可见区域
            if image_ratio > self.screen_ratio:
                # 图片比屏幕更宽，会裁剪左右部分
                visible_width_ratio = self.screen_ratio / image_ratio
                visible_width = thumb_width * visible_width_ratio
                
                # 可见区域居中
                visible_x1 = thumb_x1 + (thumb_width - visible_width) / 2
                visible_x2 = visible_x1 + visible_width
                
                # 绘制左右遮挡区域
                if visible_x1 > thumb_x1:
                    canvas.create_rectangle(thumb_x1, thumb_y1, visible_x1, thumb_y2,
                                          fill="black", stipple="gray25", outline="",
                                          tags="thumbnail_overlay")
                if visible_x2 < thumb_x2:
                    canvas.create_rectangle(visible_x2, thumb_y1, thumb_x2, thumb_y2,
                                          fill="black", stipple="gray25", outline="",
                                          tags="thumbnail_overlay")
            else:
                # 图片比屏幕更高，会裁剪上下部分
                visible_height_ratio = image_ratio / self.screen_ratio
                visible_height = thumb_height * visible_height_ratio
                
                # 可见区域居中
                visible_y1 = thumb_y1 + (thumb_height - visible_height) / 2
                visible_y2 = visible_y1 + visible_height
                
                # 绘制上下遮挡区域
                if visible_y1 > thumb_y1:
                    canvas.create_rectangle(thumb_x1, thumb_y1, thumb_x2, visible_y1,
                                          fill="black", stipple="gray25", outline="",
                                          tags="thumbnail_overlay")
                if visible_y2 < thumb_y2:
                    canvas.create_rectangle(thumb_x1, visible_y2, thumb_x2, thumb_y2,
                                          fill="black", stipple="gray25", outline="",
                                          tags="thumbnail_overlay")
                                          
        except Exception as e:
            print(f"绘制缩略图遮挡失败: {e}")

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
        self.slider = tk.Scale(self.top, from_=0.1, to=3.0, resolution=0.1, orient=tk.HORIZONTAL, label="额外缩放", command=self.update_zoom)
        self.slider.set(1.0)
        self.slider.pack(fill=tk.X)

        self.min_zoom = float(self.slider["from"])
        self.max_zoom = float(self.slider["to"])
        self._ignore_zoom_callback = False
        
        # 添加显示屏幕比例遮挡的勾选框
        self.show_screen_ratio_var = tk.BooleanVar(value=False)
        self.screen_ratio_checkbox = tk.Checkbutton(self.top, text="显示屏幕比例遮挡",
                                                   variable=self.show_screen_ratio_var,
                                                   command=self.display_image)
        self.screen_ratio_checkbox.pack(fill=tk.X, padx=5)
        
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

        # 拖动状态
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.view_offset_x = 0
        self.view_offset_y = 0

        self.photo = None
        self.cached_image_size = None
        self.cached_canvas_size = None
        self.image_item = None
        self.visible_size = None
        self.max_offset_x = 0
        self.max_offset_y = 0

        self.canvas.configure(cursor="fleur")

        # 绑定拖动与缩放事件
        self.canvas.bind("<ButtonPress-1>", self.start_drag)
        self.canvas.bind("<B1-Motion>", self.do_drag)
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<Button-4>", lambda event: self.on_mousewheel(event, wheel_delta=1))
        self.canvas.bind("<Button-5>", lambda event: self.on_mousewheel(event, wheel_delta=-1))
        self.canvas.bind("<Enter>", lambda event: self.canvas.focus_set())

        self.display_image()

    def start_drag(self, event):
        self.canvas.focus_set()
        self.drag_start_x = event.x
        self.drag_start_y = event.y

    def do_drag(self, event):
        dx = event.x - self.drag_start_x
        dy = event.y - self.drag_start_y
        self.drag_start_x = event.x
        self.drag_start_y = event.y

        self.view_offset_x += dx
        self.view_offset_y += dy
        self._clamp_offsets()
        self._update_canvas_position()

    def _clamp_offsets(self):
        self.view_offset_x = max(-self.max_offset_x, min(self.max_offset_x, self.view_offset_x))
        self.view_offset_y = max(-self.max_offset_y, min(self.max_offset_y, self.view_offset_y))

    def _update_canvas_position(self, redraw_overlay=True):
        if self.image_item is None or self.cached_canvas_size is None:
            return
        canvas_width, canvas_height = self.cached_canvas_size
        image_center_x = canvas_width / 2 + self.view_offset_x
        image_center_y = canvas_height / 2 + self.view_offset_y
        self.canvas.coords(self.image_item, image_center_x, image_center_y)

        if redraw_overlay:
            self.canvas.delete("screen_ratio_overlay")
            if self.show_screen_ratio_var.get() and self.visible_size is not None:
                self.draw_screen_ratio_overlay(canvas_width, canvas_height, self.cached_image_size, (image_center_x, image_center_y), self.visible_size)
        else:
            if not self.show_screen_ratio_var.get():
                self.canvas.delete("screen_ratio_overlay")

        bbox = self.canvas.bbox(self.image_item)
        if bbox:
            self.canvas.config(scrollregion=bbox)

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
        new_width = max(int(orig_width * effective_zoom), 1)
        new_height = max(int(orig_height * effective_zoom), 1)
        new_size = (new_width, new_height)

        needs_resample = self.cached_image_size != new_size or self.photo is None
        if needs_resample:
            try:
                resized = self.original_image.resize(new_size, Image.Resampling.HAMMING)
                self.photo = ImageTk.PhotoImage(resized)
            except Exception as e:
                print("图片缩放失败:", e)
                return
            if self.image_item is None:
                self.image_item = self.canvas.create_image(canvas_width / 2, canvas_height / 2, anchor=tk.CENTER, image=self.photo)
            else:
                self.canvas.itemconfigure(self.image_item, image=self.photo)
            self.cached_image_size = new_size
        elif self.image_item is None:
            self.image_item = self.canvas.create_image(canvas_width / 2, canvas_height / 2, anchor=tk.CENTER, image=self.photo)

        self.max_offset_x = max((new_width - canvas_width) / 2, 0)
        self.max_offset_y = max((new_height - canvas_height) / 2, 0)
        self.cached_canvas_size = (canvas_width, canvas_height)

        visible_size = None
        if self.show_screen_ratio_var.get():
            try:
                image_ratio = orig_width / orig_height
            except ZeroDivisionError:
                image_ratio = 0
            if image_ratio > 0:
                if image_ratio > self.screen_ratio:
                    visible_width = new_height * self.screen_ratio
                    visible_height = new_height
                else:
                    visible_width = new_width
                    visible_height = new_width / self.screen_ratio
                visible_width = min(visible_width, new_width)
                visible_height = min(visible_height, new_height)
                visible_size = (visible_width, visible_height)
        self.visible_size = visible_size

        self._clamp_offsets()
        self._update_canvas_position(redraw_overlay=True)

        self.top.title(f"预览: {os.path.basename(self.img_path)}")
        info = get_image_info(self.img_path)
        if info is not None:
            width, height, ratio, file_size = info
            total_score = compute_score(self.img_path, self.screen_ratio, self.max_area, self.max_size)
            aspect_score = aspect_ratio_score(ratio, self.screen_ratio)
            info_text = f"尺寸: {width}x{height}, 大小: {file_size} bytes, 总分: {total_score:.1f}, 比例分: {aspect_score:.1f}"
            self.info_label.config(text=info_text)

    def update_zoom(self, val):
        if self._ignore_zoom_callback:
            return
        try:
            zoom_value = float(val)
        except Exception as e:
            print("更新缩放出错:", e)
            return
        self.change_zoom(zoom_value)

    def change_zoom(self, new_zoom, focal_point=None, update_slider=False, reset_offsets=False):
        new_zoom = max(self.min_zoom, min(self.max_zoom, new_zoom))
        try:
            orig_width, orig_height = self.original_image.size
        except Exception as e:
            print("获取图片尺寸失败:", e)
            return

        if reset_offsets:
            self.view_offset_x = 0
            self.view_offset_y = 0

        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        if canvas_width > 1 and canvas_height > 1:
            base_zoom = min(canvas_width / orig_width, canvas_height / orig_height)
            old_effective_zoom = base_zoom * self.zoom_level
            new_effective_zoom = base_zoom * new_zoom
            old_width = orig_width * old_effective_zoom
            old_height = orig_height * old_effective_zoom
            new_width = orig_width * new_effective_zoom
            new_height = orig_height * new_effective_zoom

            if not reset_offsets and old_width > 0 and old_height > 0:
                image_center_x = canvas_width / 2 + self.view_offset_x
                image_center_y = canvas_height / 2 + self.view_offset_y
                if focal_point is not None:
                    rel_x = focal_point[0] - image_center_x
                    rel_y = focal_point[1] - image_center_y
                    ratio_x = rel_x / old_width
                    ratio_y = rel_y / old_height
                    new_center_x = focal_point[0] - ratio_x * new_width
                    new_center_y = focal_point[1] - ratio_y * new_height
                    self.view_offset_x = new_center_x - canvas_width / 2
                    self.view_offset_y = new_center_y - canvas_height / 2
                else:
                    scale_x = new_width / old_width if old_width != 0 else 1
                    scale_y = new_height / old_height if old_height != 0 else 1
                    self.view_offset_x *= scale_x
                    self.view_offset_y *= scale_y

        self.zoom_level = new_zoom
        if update_slider:
            self._ignore_zoom_callback = True
            try:
                self.slider.set(round(new_zoom, 3))
            finally:
                self._ignore_zoom_callback = False

        self.display_image()

    def on_mousewheel(self, event, wheel_delta=None):
        delta = wheel_delta if wheel_delta is not None else event.delta
        if delta == 0:
            return
        step = 1.1 if delta > 0 else 1 / 1.1
        new_zoom = self.zoom_level * step
        self.change_zoom(new_zoom, focal_point=(event.x, event.y), update_slider=True)

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
        self.photo = None
        self.cached_image_size = None
        self.visible_size = None
        self.view_offset_x = 0
        self.view_offset_y = 0
        self.change_zoom(1.0, update_slider=True, reset_offsets=True)

    def open_external_and_copy(self):
        open_external_and_copy(self.img_path)

    def copy_current_file(self):
        copy_file_to_clipboard(self.img_path)

    def draw_screen_ratio_overlay(self, canvas_width, canvas_height, image_size, image_center, visible_size):
        """
        绘制屏幕比例的半透明遮挡，显示以覆盖模式显示时能够看到的部分
        """
        try:
            img_display_width, img_display_height = image_size
            visible_width, visible_height = visible_size
            image_center_x, image_center_y = image_center

            img_x1 = image_center_x - img_display_width / 2
            img_y1 = image_center_y - img_display_height / 2
            img_x2 = image_center_x + img_display_width / 2
            img_y2 = image_center_y + img_display_height / 2

            visible_x1 = image_center_x - visible_width / 2
            visible_y1 = image_center_y - visible_height / 2
            visible_x2 = image_center_x + visible_width / 2
            visible_y2 = image_center_y + visible_height / 2

            visible_x1 = max(visible_x1, img_x1)
            visible_y1 = max(visible_y1, img_y1)
            visible_x2 = min(visible_x2, img_x2)
            visible_y2 = min(visible_y2, img_y2)

            overlay_fill = "black"
            overlay_stipple = "gray50"
            overlay_tag = "screen_ratio_overlay"

            self.canvas.create_rectangle(img_x1, img_y1, img_x2, visible_y1,
                                         fill=overlay_fill, stipple=overlay_stipple, outline="",
                                         tags=overlay_tag)
            self.canvas.create_rectangle(img_x1, visible_y2, img_x2, img_y2,
                                         fill=overlay_fill, stipple=overlay_stipple, outline="",
                                         tags=overlay_tag)
            self.canvas.create_rectangle(img_x1, visible_y1, visible_x1, visible_y2,
                                         fill=overlay_fill, stipple=overlay_stipple, outline="",
                                         tags=overlay_tag)
            self.canvas.create_rectangle(visible_x2, visible_y1, img_x2, visible_y2,
                                         fill=overlay_fill, stipple=overlay_stipple, outline="",
                                         tags=overlay_tag)

        except Exception as e:
            print(f"绘制屏幕比例遮挡失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="图片浏览器 GUI")
    parser.add_argument("--folder", help="要扫描的文件夹路径")
    parser.add_argument("--page-size", type=int, default=12, help="每页显示图片数量，默认12个")
    parser.add_argument("--ratio", help="指定目标宽高比，格式 a:b")
    args = parser.parse_args()
    desired_ratio = None
    if args.ratio:
        try:
            a, b = args.ratio.split(":")
            desired_ratio = float(a) / float(b)
        except Exception:
            print("比例格式应为 a:b，其中 a、b 为数字")
            sys.exit(1)
    root = tk.Tk()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    w = int(sw * 0.75)
    h = int(sh * 0.75)
    x = (sw - w) // 2
    y = (sh - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")
    app = ImageBrowser(root, folder=args.folder, page_size=args.page_size, desired_ratio=desired_ratio)
    root.mainloop()


if __name__ == "__main__":
    main()
