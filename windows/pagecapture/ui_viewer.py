"""Owned page review window; reads local images and cached OCR only."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk


class PageViewer(tk.Toplevel):
    """A full-page image viewer with a copyable recognized-text tab."""

    def __init__(self, owner: tk.Tk, *, on_close=None) -> None:
        super().__init__(owner)
        self.transient(owner)
        self.title("Page review · Page Capture")
        self.geometry("900x760")
        self.minsize(600, 460)
        self.configure(bg="#f7f7f5")
        self._on_close = on_close
        self._source: Image.Image | None = None
        self._photo = None
        self._fit = True
        self._scale = 1.0
        self._resize_job = None
        self._closed = False
        self.protocol("WM_DELETE_WINDOW", self.close)
        header = tk.Frame(self, bg="#f7f7f5")
        header.pack(fill="x", padx=18, pady=(16, 10))
        self.page_title = tk.StringVar(value="Page review")
        tk.Label(header, textvariable=self.page_title, bg="#f7f7f5", fg="#202522",
                 font=("Segoe UI", 16, "bold")).pack(side="left")
        self.zoom_label = tk.StringVar(value="Fit")
        tk.Label(header, textvariable=self.zoom_label, bg="#f7f7f5", fg="#68726c").pack(side="right", padx=(12, 0))
        for text, action in (("+", lambda: self.zoom(1.25)), ("−", lambda: self.zoom(0.8)),
                             ("100%", self.actual_size), ("Fit", self.fit_page)):
            tk.Button(header, text=text, command=action, relief="flat", bd=0,
                      bg="white", fg="#202522", activebackground="#e7ece7",
                      padx=12, pady=6, cursor="hand2", highlightthickness=1,
                      highlightbackground="#dfe3de").pack(side="right", padx=(5, 0))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=18)
        image_tab = tk.Frame(self.tabs, bg="#e9ede7")
        text_tab = tk.Frame(self.tabs, bg="white")
        self.tabs.add(image_tab, text="  Page image  ")
        self.tabs.add(text_tab, text="  Recognized text  ")
        image_tab.rowconfigure(0, weight=1)
        image_tab.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(image_tab, bg="#e9ede7", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(image_tab, orient="vertical", command=self.canvas.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(image_tab, orient="horizontal", command=self.canvas.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
        self.canvas.bind("<Configure>", self._resized)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._horizontal_wheel)
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        text_tab.rowconfigure(1, weight=1)
        text_tab.columnconfigure(0, weight=1)
        tk.Label(text_tab, text="Recognized text is a local reading aid. Select text to copy it.",
                 bg="white", fg="#68726c", anchor="w", padx=16, pady=12).grid(row=0, column=0, sticky="ew")
        self.text = tk.Text(text_tab, wrap="word", bg="white", fg="#202522", relief="flat",
                            padx=20, pady=12, font=("Segoe UI", 12), spacing1=3, spacing3=6,
                            selectbackground="#d8e4db", selectforeground="#202522", state="disabled")
        self.text.grid(row=1, column=0, sticky="nsew")
        text_scroll = ttk.Scrollbar(text_tab, orient="vertical", command=self.text.yview)
        text_scroll.grid(row=1, column=1, sticky="ns")
        self.text.configure(yscrollcommand=text_scroll.set)
        self.text.bind("<Control-a>", self._select_all_text)
        tk.Label(self, text="Fit: 0   ·   Actual size: 1   ·   Zoom: + / − or Ctrl + wheel   ·   Drag to pan   ·   Esc to close",
                 bg="#f7f7f5", fg="#68726c", font=("Segoe UI", 9)).pack(anchor="w", padx=20, pady=12)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<KeyPress-plus>", lambda e: self._keyboard_zoom(1.25, e))
        self.bind("<KeyPress-equal>", lambda e: self._keyboard_zoom(1.25, e))
        self.bind("<KeyPress-minus>", lambda e: self._keyboard_zoom(0.8, e))
        self.bind("<KeyPress-0>", lambda e: self._keyboard_view(self.fit_page))
        self.bind("<KeyPress-1>", lambda e: self._keyboard_view(self.actual_size))

    def set_page(self, path: Path, page, number: int, total: int, *, show_text=False) -> None:
        with Image.open(path) as image:
            source = image.copy()
        if self._source is not None:
            self._source.close()
        self._source = source
        self.page_title.set(f"Page {number} of {total}  ·  {page.width:,} × {page.height:,}")
        self.title(f"Page {number} · Page Capture")
        recognized = (page.ocr or {}).get("text", "")
        if not isinstance(recognized, str):
            recognized = str(recognized)
        text = recognized if recognized.strip() else (
            "No recognized text is saved for this page.\n\n"
            "Return to the workspace and choose Recognize text to read this page locally."
        )
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.configure(state="disabled")
        self.text.yview_moveto(0)
        self.tabs.select(1 if show_text else 0)
        self._fit = True
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.after_idle(self._render)
        self.deiconify()
        self.lift()
        self.focus_set()

    def _resized(self, _event=None) -> None:
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(75, self._render)

    def fit_page(self) -> None:
        self.tabs.select(0)
        self._fit = True
        self._render()

    def actual_size(self) -> None:
        self.tabs.select(0)
        self._fit = False
        self._scale = 1.0
        self._render()

    def zoom(self, factor: float) -> None:
        self.tabs.select(0)
        self._fit = False
        self._scale = min(4.0, max(0.05, self._scale * factor))
        self._render()

    def _render(self) -> None:
        self._resize_job = None
        if self._closed or self._source is None:
            return
        viewport_w = max(100, self.canvas.winfo_width())
        viewport_h = max(100, self.canvas.winfo_height())
        old_region = self.canvas.bbox("page")
        old_center = None
        if old_region:
            old_center = ((self.canvas.canvasx(viewport_w / 2) - old_region[0]) / max(1, old_region[2] - old_region[0]),
                          (self.canvas.canvasy(viewport_h / 2) - old_region[1]) / max(1, old_region[3] - old_region[1]))
        if self._fit:
            self._scale = min((viewport_w - 36) / self._source.width,
                              (viewport_h - 36) / self._source.height, 1.0)
        width = max(1, int(self._source.width * self._scale))
        height = max(1, int(self._source.height * self._scale))
        rendered = self._source.resize((width, height), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(rendered)
        self.canvas.delete("all")
        extent_w, extent_h = max(viewport_w, width + 36), max(viewport_h, height + 36)
        left, top = (extent_w - width) / 2, (extent_h - height) / 2
        self.canvas.create_rectangle(left + 3, top + 3, left + width + 3, top + height + 3,
                                     fill="#cfd6cb", outline="")
        self.canvas.create_image(left, top, anchor="nw", image=self._photo, tags="page")
        self.canvas.configure(scrollregion=(0, 0, extent_w, extent_h))
        self.zoom_label.set(f"{'Fit · ' if self._fit else ''}{round(self._scale * 100)}%")
        if self._fit:
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        elif old_center:
            self.canvas.xview_moveto(max(0, (left + old_center[0] * width - viewport_w / 2) / extent_w))
            self.canvas.yview_moveto(max(0, (top + old_center[1] * height - viewport_h / 2) / extent_h))

    def _wheel(self, event):
        if event.state & 0x0004:
            self.zoom(1.25 if event.delta > 0 else 0.8)
        else:
            self.canvas.yview_scroll(-3 if event.delta > 0 else 3, "units")
        return "break"

    def _horizontal_wheel(self, event):
        self.canvas.xview_scroll(-3 if event.delta > 0 else 3, "units")
        return "break"

    def _keyboard_zoom(self, factor, _event):
        if self.tabs.index("current") == 0:
            self.zoom(factor)
            return "break"
        return None

    def _keyboard_view(self, callback):
        if self.tabs.index("current") == 0:
            callback()
            return "break"
        return None

    def _select_all_text(self, _event):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        if self._source is not None:
            self._source.close()
        if self._on_close:
            self._on_close()
        self.destroy()
