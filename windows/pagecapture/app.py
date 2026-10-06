"""Windows workspace built on the established Win32 capture engine.

Capture coordinates, monitor overlays and stable-page detection live in
legacy_engine. This module owns presentation and the persisted project workflow.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable

from PIL import Image, ImageTk

from . import legacy_engine as engine
from .ui_viewer import PageViewer
from .core import (
    JobCancelled, Session, documents_directory, export_pdf, import_files,
    list_sessions, ocr_available, recognize_pages,
)

BG = "#f7f7f5"
SIDE = "#eeefed"
WHITE = "#ffffff"
INK = "#202522"
MUTED = "#68726c"
LINE = "#dfe3de"
GREEN = "#187153"


class PageCaptureApp(engine.PageCaptureApp):
    """One project owner; all Tk calls occur on the Tk thread."""

    def __init__(self, root: tk.Tk, project_dir: Path | None = None) -> None:
        self.root = root
        root.title("Page Capture")
        root.geometry("1100x740")
        root.minsize(960, 680)
        root.configure(bg=BG)
        root.attributes("-topmost", False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.option_add("*Font", ("Segoe UI", 10))
        self.capture_box = None
        self.selected_virtual_bounds = None
        self.pages: list[Path] = []
        self.last_signature = None
        self.capture_busy = False
        self.manual_capture_job = None
        self.region_selector = None
        self.region_selector_job = None
        self.auto_watching = False
        self.auto_candidate = None
        self.auto_candidate_signature = None
        self.auto_candidate_since = 0.0
        self.auto_stable_polls = 0
        self._closing = False
        self._closed = False
        self._job_thread: threading.Thread | None = None
        self._job_cancel = threading.Event()
        self._events: queue.Queue = queue.Queue()
        self._preview_job = None
        self._preview_photo = None
        self._page_viewer: PageViewer | None = None
        self._thumbnail_photos: list[ImageTk.PhotoImage] = []
        self._thumbnail_cards: list[tk.Frame] = []
        self._gallery_paths: list[Path] = []
        self._active_index = -1
        self._library_entries: list[dict] = []
        self._action_buttons: list[tk.Button] = []
        self.sessions_root = documents_directory() / "Page Capture Sessions"
        self.exports_root = documents_directory() / "Page Capture Exports"
        self.preferences_path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Page Capture" / "preferences.json"
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self.session = self._initial_session(project_dir)
        self.session.set_region(None)
        self.session_dir = self.session.directory
        self.delay_seconds = tk.DoubleVar(value=0.45)
        self.compact_pdf = tk.BooleanVar(value=False)
        self.sound_enabled = tk.BooleanVar(value=False)
        self.pin_on_top = tk.BooleanVar(value=False)
        self.ocr_capability = ocr_available()
        self.searchable_pdf = tk.BooleanVar(value=bool(self.ocr_capability.get("available")))
        self.language = tk.StringVar(value="Automatic")
        self.status_text = tk.StringVar(value="Select a page area or import a document to begin.")
        self.region_text = tk.StringVar(value="No area selected · F6 to select")
        self.count_text = tk.StringVar(value="0 pages")
        self.title_text = tk.StringVar(value=self.session.title)
        self.page_text = tk.StringVar(value="Your pages appear here")
        self.page_detail = tk.StringVar(value="Capture a page or import images and PDFs.")
        self.progress_text = tk.StringVar(value="All work stays on this computer")
        self._build_ui()
        self._refresh_pages()
        self._refresh_library()
        self._save_preference()
        self.hotkey_events: queue.Queue = queue.Queue()
        self.hotkeys = engine.HotkeyListener(self.hotkey_events)
        self.hotkeys.start()
        self._hotkey_job = root.after(75, self._process_hotkey_events)
        self._event_job = root.after(80, self._drain_events)
        root.bind("<Left>", lambda e: self._navigate(-1, e))
        root.bind("<Right>", lambda e: self._navigate(1, e))
        root.bind("<Home>", lambda e: self._navigate_to(0, e))
        root.bind("<End>", lambda e: self._navigate_to(len(self.pages) - 1, e))
        root.bind("<Control-z>", lambda e: self.undo_last())
        root.bind("<Control-Shift-Z>", lambda e: self.restore_last())
        if engine.DPI_MODE not in ("per-monitor-v2", "per-monitor-v1"):
            self.set_status("Per-monitor scaling support is unavailable. Check the selected area when using different display scales.", "warning")

    def _initial_session(self, project_dir: Path | None) -> Session:
        if project_dir is not None:
            return Session.load(Path(project_dir))
        try:
            saved = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            directory = Path(saved["last_project"])
            if directory.is_dir():
                return Session.load(directory)
        except (OSError, ValueError, KeyError, RuntimeError):
            pass
        return Session.create(self.sessions_root, "Untitled capture")

    def _save_preference(self) -> None:
        try:
            self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.preferences_path.with_suffix(".tmp")
            temp.write_text(json.dumps({"last_project": str(self.session.directory)}), encoding="utf-8")
            os.replace(temp, self.preferences_path)
        except OSError as error:
            self.set_status(f"Project saved. Windows could not remember the last project: {error}", "warning")

    @staticmethod
    def _label(parent, text=None, *, variable=None, size=10, bold=False, color=INK, bg=WHITE, **kwargs):
        return tk.Label(parent, text=text, textvariable=variable, bg=bg, fg=color,
                        font=("Segoe UI", size, "bold" if bold else "normal"), **kwargs)

    def _button(self, parent, text, command, *, primary=False, tracked=True):
        button = tk.Button(parent, text=text, command=command,
                           bg=GREEN if primary else WHITE, fg=WHITE if primary else INK,
                           activebackground="#145c45" if primary else "#e7ece7",
                           activeforeground=WHITE if primary else INK,
                           disabledforeground="#a0a7a1", relief="flat", bd=0,
                           padx=13, pady=9, cursor="hand2", takefocus=True,
                           highlightthickness=1, highlightbackground=GREEN if primary else LINE)
        if tracked:
            self._action_buttons.append(button)
        return button

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TProgressbar", background=GREEN, troughcolor=LINE, borderwidth=0)
        style.configure("TCheckbutton", background=BG, foreground=INK)
        style.configure("TCombobox", padding=3)
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, bg=SIDE, width=224)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self._label(sidebar, "▤  Page Capture", size=17, bold=True, bg=SIDE).pack(anchor="w", padx=20, pady=(24, 6))
        self._label(sidebar, "A home for your pages", color=MUTED, bg=SIDE).pack(anchor="w", padx=21, pady=(0, 24))
        self._button(sidebar, "+  New project", self.new_project).pack(fill="x", padx=16, pady=(0, 8))
        self._button(sidebar, "Open project…", self.open_project).pack(fill="x", padx=16, pady=(0, 24))
        self._label(sidebar, "PROJECTS", size=9, bold=True, color=MUTED, bg=SIDE).pack(anchor="w", padx=21, pady=(0, 9))
        self.library = tk.Listbox(sidebar, bg=SIDE, fg=INK, selectbackground="#d8e4db", selectforeground=INK,
                                  relief="flat", bd=0, highlightthickness=0, activestyle="none",
                                  exportselection=False, font=("Segoe UI", 10), height=8)
        self.library.pack(fill="both", expand=True, padx=14)
        self.library.bind("<<ListboxSelect>>", self._library_selected)
        self._button(sidebar, "Show project folder", self.show_project_folder, tracked=False).pack(fill="x", padx=16, pady=(16, 8))
        self._button(sidebar, "Show exports", self.show_exports_folder, tracked=False).pack(fill="x", padx=16, pady=(0, 12))
        self._label(sidebar, "LOCAL WORKSPACE\nOriginals remain recoverable", size=9, color=MUTED,
                    bg=SIDE, justify="left").pack(anchor="w", padx=20, pady=(0, 22))
        main = tk.Frame(shell, bg=BG)
        main.pack(side="left", fill="both", expand=True, padx=24, pady=22)
        header = tk.Frame(main, bg=BG)
        header.pack(fill="x")
        self._label(header, variable=self.title_text, size=23, bold=True, bg=BG).pack(side="left")
        self._label(header, variable=self.count_text, color=MUTED, bg=BG).pack(side="right", pady=(8, 0))
        self._label(main, "Capture, review, and make a PDF you can search.", color=MUTED, bg=BG).pack(anchor="w", pady=(5, 18))
        toolbar = tk.Frame(main, bg=BG)
        toolbar.pack(fill="x")
        for text, command in (("Select area  F6", self.select_region), ("Capture  F8", self.capture_page), ("Import…", self.import_documents)):
            self._button(toolbar, text, command).pack(side="left", padx=(0, 7))
        self.export_button = self._button(toolbar, "Export PDF  F9", self.finish_pdf, primary=True)
        self.export_button.pack(side="right")
        self.ocr_button = self._button(toolbar, "Recognize text", self.recognize_text)
        self.ocr_button.pack(side="right", padx=(0, 7))
        self._label(main, variable=self.region_text, size=9, color=MUTED, bg=BG).pack(anchor="w", pady=(10, 12))
        preview_card = tk.Frame(main, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        preview_card.pack(fill="both", expand=True)
        preview_head = tk.Frame(preview_card, bg=WHITE)
        preview_head.pack(fill="x", padx=17, pady=(12, 8))
        self._label(preview_head, variable=self.page_text, bold=True).pack(side="left")
        next_button = self._button(preview_head, "›", lambda: self._navigate(1), tracked=False)
        next_button.configure(pady=2, padx=10)
        next_button.pack(side="right", padx=(4, 0))
        previous_button = self._button(preview_head, "‹", lambda: self._navigate(-1), tracked=False)
        previous_button.configure(pady=2, padx=10)
        previous_button.pack(side="right")
        for text, command in (("Open page", self.open_page_viewer), ("Text", self.show_recognized_text)):
            button = self._button(preview_head, text, command, tracked=False)
            button.configure(pady=2, padx=9)
            button.pack(side="right", padx=(0, 7))
        self.preview = tk.Canvas(preview_card, bg="#f0f2ef", height=180, highlightthickness=0)
        self.preview.pack(fill="both", expand=True, padx=16)
        self.preview.bind("<Configure>", self._schedule_preview)
        self.preview.bind("<Double-Button-1>", lambda e: self.open_page_viewer())
        self._label(preview_card, variable=self.page_detail, size=9, color=MUTED).pack(anchor="w", padx=18, pady=(8, 11))
        gallery_box = tk.Frame(main, bg=BG)
        gallery_box.pack(fill="x", pady=(12, 0))
        self.gallery_canvas = tk.Canvas(gallery_box, bg=BG, height=102, highlightthickness=0)
        self.gallery_canvas.pack(fill="x")
        self.gallery_content = tk.Frame(self.gallery_canvas, bg=BG)
        self.gallery_canvas.create_window((0, 0), window=self.gallery_content, anchor="nw")
        self.gallery_content.bind("<Configure>", lambda e: self.gallery_canvas.configure(scrollregion=self.gallery_canvas.bbox("all")))
        self.gallery_canvas.bind("<MouseWheel>", self._scroll_gallery)
        ttk.Scrollbar(gallery_box, orient="horizontal", command=self.gallery_canvas.xview).pack(fill="x")
        self.gallery_canvas.configure(xscrollcommand=lambda a, b: None)
        # Set after construction so the native scrollbar reflects the visible pages.
        scrollbar = gallery_box.winfo_children()[-1]
        self.gallery_canvas.configure(xscrollcommand=scrollbar.set)
        settings = tk.Frame(main, bg=BG)
        settings.pack(fill="x", pady=(12, 0))
        self.auto_button = self._button(settings, "Automatic  F10", self.toggle_auto_watch)
        self.auto_button.pack(side="left", padx=(0, 8))
        self._button(settings, "Undo  F7", self.undo_last).pack(side="left", padx=(0, 6))
        self._button(settings, "Restore", self.restore_last).pack(side="left")
        ttk.Checkbutton(settings, text="Pin on top", variable=self.pin_on_top,
                        command=lambda: self.root.attributes("-topmost", self.pin_on_top.get())).pack(side="right")
        options = tk.Frame(main, bg=BG)
        options.pack(fill="x", pady=(9, 8))
        ttk.Checkbutton(options, text="Searchable PDF", variable=self.searchable_pdf).pack(side="left")
        ttk.Checkbutton(options, text="Compact PDF", variable=self.compact_pdf).pack(side="left", padx=(10, 12))
        self._label(options, "Settle", size=9, color=MUTED, bg=BG).pack(side="left")
        ttk.Spinbox(options, textvariable=self.delay_seconds, from_=0.2, to=3, increment=0.1, width=4).pack(side="left", padx=5)
        self._label(options, "s", size=9, color=MUTED, bg=BG).pack(side="left")
        self.language_box = ttk.Combobox(options, textvariable=self.language,
                                      values=["Automatic"] + self.ocr_capability.get("languages", []),
                                      state="readonly", width=12)
        self.language_box.pack(side="right")
        self._label(options, "OCR language", size=9, color=MUTED, bg=BG).pack(side="right", padx=7)
        progress_row = tk.Frame(main, bg=BG)
        progress_row.pack(fill="x")
        self.progress = ttk.Progressbar(progress_row, maximum=1, value=0)
        self.progress.pack(side="left", fill="x", expand=True, pady=8)
        self.stop_button = self._button(progress_row, "Stop", self.stop_job, tracked=False)
        self.stop_button.pack(side="right", padx=(10, 0))
        self.stop_button.configure(state="disabled")
        self._label(main, variable=self.progress_text, size=9, color=MUTED, bg=BG).pack(anchor="w")
        self.status_label = self._label(main, variable=self.status_text, size=9, color=MUTED,
                                      bg=BG, anchor="w", justify="left", wraplength=770)
        self.status_label.pack(fill="x", pady=(7, 0))
        self._set_busy(False)

    def set_status(self, message: str, _kind: str = "normal") -> None:
        self.status_text.set(message)
        if hasattr(self, "status_label"):
            self.status_label.configure(fg="#9a5720" if _kind == "warning" else MUTED)

    def show_toolbar(self) -> None:
        if self._closed:
            return
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", self.pin_on_top.get())

    def _blocked(self) -> bool:
        if self._job_thread is not None or self._closing:
            self.set_status("Wait for the current job, or press Stop.")
            return True
        return False

    def select_region(self) -> None:
        if not self._blocked():
            self._close_page_viewer()
            super().select_region()

    def set_region(self, capture_box) -> None:
        self.session.set_region(capture_box)
        super().set_region(capture_box)

    def capture_page(self) -> None:
        if not self._blocked():
            self._close_page_viewer()
            super().capture_page()

    def toggle_auto_watch(self) -> None:
        if not self._blocked():
            self._close_page_viewer()
            super().toggle_auto_watch()

    def start_auto_watch(self) -> None:
        super().start_auto_watch()
        if self.auto_watching:
            self.auto_button.configure(text="Stop automatic  F10")
            self.set_status("Watching for stable pages. Turn pages normally; F10 returns to review.")

    def stop_auto_watch(self) -> None:
        super().stop_auto_watch()
        self.auto_button.configure(text="Automatic  F10")
        self.set_status("Automatic capture stopped. Review your pages or export a PDF.")

    def _save_page(self, image: Image.Image, skip_duplicate: bool = True) -> bool:
        # Perceptual signatures are for change detection only. Core checks exact
        # duplicates across the complete project before persisting a capture.
        page = self.session.add_image(image, source_name="Screen capture")
        if page is None:
            self.last_signature = engine.frame_signature(image)
            return False
        self.last_signature = engine.frame_signature(image)
        self._active_index = len(self.session.pages) - 1
        self._refresh_pages()
        self._refresh_library()
        self.set_status(f"Captured page {len(self.pages)}. Original saved in this project.")
        return True

    def _refresh_pages(self) -> None:
        self.pages = list(self.session.page_paths)
        self.session_dir = self.session.directory
        self.title_text.set(self.session.title)
        self.count_text.set(f"{len(self.pages)} {'page' if len(self.pages) == 1 else 'pages'}")
        self._active_index = min(max(self._active_index, 0), len(self.pages) - 1)
        unchanged = self._gallery_paths == self.pages
        can_append = bool(self._gallery_paths) and self.pages[:len(self._gallery_paths)] == self._gallery_paths
        if unchanged:
            start_index = len(self.pages)
        elif can_append:
            start_index = len(self._gallery_paths)
        else:
            for card in self._thumbnail_cards:
                card.destroy()
            self._thumbnail_cards.clear()
            self._thumbnail_photos.clear()
            start_index = 0
        for index in range(start_index, len(self.pages)):
            path = self.pages[index]
            card = tk.Frame(self.gallery_content, bg=WHITE, highlightthickness=2,
                            highlightbackground=GREEN if index == self._active_index else LINE)
            card.grid(row=0, column=index, padx=(0, 7), pady=2)
            try:
                with Image.open(path) as source:
                    thumbnail = source.copy()
                    thumbnail.thumbnail((76, 64), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(thumbnail)
                self._thumbnail_photos.append(photo)
                label = tk.Label(card, image=photo, bg=WHITE, width=83, height=65)
            except Exception:
                label = self._label(card, "Preview\nunavailable", size=8, color=MUTED, width=11, height=4)
            label.pack(padx=3, pady=(3, 0))
            number = self._label(card, str(index + 1), size=9, bold=True)
            number.pack(pady=(1, 3))
            for widget in (card, label, number):
                widget.bind("<Button-1>", lambda e, i=index: self._select_page(i))
            self._thumbnail_cards.append(card)
        if not self.pages and not self._thumbnail_cards:
            placeholder = self._label(self.gallery_content, "No pages yet · originals will be saved as you work", color=MUTED, bg=BG)
            placeholder.grid(row=0, column=0, padx=12, pady=30)
            self._thumbnail_cards.append(placeholder)
        self._gallery_paths = list(self.pages)
        if self.pages:
            for index, card in enumerate(self._thumbnail_cards):
                card.configure(highlightbackground=GREEN if index == self._active_index else LINE)
        if self.pages:
            try:
                with Image.open(self.pages[-1]) as last:
                    self.last_signature = engine.frame_signature(last)
            except Exception:
                self.last_signature = None
        else:
            self.last_signature = None
        self._render_preview()
        if self._page_viewer is not None:
            if not self.pages:
                self._close_page_viewer()
            else:
                show_text = self._page_viewer.tabs.index("current") == 1
                self.open_page_viewer(show_text=show_text)

    def _schedule_preview(self, _event=None) -> None:
        if self._preview_job is not None:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(70, self._render_preview)

    def _render_preview(self) -> None:
        self._preview_job = None
        self.preview.delete("all")
        width = max(100, self.preview.winfo_width())
        height = max(100, self.preview.winfo_height())
        if self._active_index < 0 or not self.pages:
            self.page_text.set("Your pages appear here")
            self.page_detail.set("Capture a page or import images and PDFs.")
            self.preview.create_text(width / 2, height / 2 - 13, text="Make room for what matters.",
                                     fill=INK, font=("Segoe UI", 18))
            self.preview.create_text(width / 2, height / 2 + 21, text="Select an area · Capture a page · Review your PDF", fill=MUTED, font=("Segoe UI", 10))
            return
        page = self.session.pages[self._active_index]
        self.page_text.set(f"Page {self._active_index + 1} of {len(self.pages)}")
        self.page_detail.set(f"{page.width:,} × {page.height:,} pixels  ·  {'Text recognized' if page.ocr else 'Image original'}  ·  Double-click to open  ·  ← → to review")
        try:
            with Image.open(self.pages[self._active_index]) as source:
                image = source.copy()
            image.thumbnail((max(40, width - 32), max(40, height - 22)), Image.Resampling.LANCZOS)
            self._preview_photo = ImageTk.PhotoImage(image)
            x, y = width / 2, height / 2
            self.preview.create_rectangle(x - image.width / 2 + 3, y - image.height / 2 + 3,
                                          x + image.width / 2 + 3, y + image.height / 2 + 3,
                                          fill="#d5dad3", outline="")
            self.preview.create_image(x, y, image=self._preview_photo)
        except Exception as error:
            self.preview.create_text(width / 2, height / 2, text=f"Preview unavailable\n{error}",
                                     fill=MUTED, width=max(60, width - 60), justify="center")

    def _select_page(self, index: int) -> None:
        if not 0 <= index < len(self.pages):
            return
        self._active_index = index
        for i, card in enumerate(self._thumbnail_cards):
            card.configure(highlightbackground=GREEN if i == index else LINE)
        self._render_preview()
        if self._page_viewer is not None:
            self.open_page_viewer(show_text=self._page_viewer.tabs.index("current") == 1)
        self.gallery_canvas.update_idletasks()
        card = self._thumbnail_cards[index]
        full_width = max(1, self.gallery_content.winfo_reqwidth())
        start, end = self.gallery_canvas.xview()
        left, right = card.winfo_x() / full_width, (card.winfo_x() + card.winfo_width()) / full_width
        if left < start:
            self.gallery_canvas.xview_moveto(left)
        elif right > end:
            self.gallery_canvas.xview_moveto(max(0, right - (end - start)))

    def _navigate(self, delta: int, event=None):
        return self._navigate_to(self._active_index + delta, event)

    def open_page_viewer(self, *, show_text=False) -> None:
        if not self.pages or self._active_index < 0:
            self.set_status("Capture or import a page to open the page viewer.")
            return
        try:
            if self._page_viewer is None or not self._page_viewer.winfo_exists():
                self._page_viewer = PageViewer(self.root, on_close=self._viewer_closed)
            self._page_viewer.set_page(self.pages[self._active_index], self.session.pages[self._active_index],
                                       self._active_index + 1, len(self.pages), show_text=show_text)
        except Exception as error:
            self._error("Could not open the page viewer", error)

    def show_recognized_text(self) -> None:
        self.open_page_viewer(show_text=True)

    def _viewer_closed(self) -> None:
        self._page_viewer = None

    def _close_page_viewer(self) -> None:
        if self._page_viewer is not None:
            self._page_viewer.close()

    def _navigate_to(self, index: int, event=None):
        if event is not None and isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox, tk.Listbox)):
            return None
        if self.pages:
            self._select_page(min(max(index, 0), len(self.pages) - 1))
        return "break"

    def _scroll_gallery(self, event):
        self.gallery_canvas.xview_scroll(-3 if event.delta > 0 else 3, "units")
        return "break"

    def undo_last(self) -> None:
        if self._blocked() or self.capture_busy:
            return
        if self.auto_watching:
            self.stop_auto_watch()
        try:
            removed = self.session.undo_last()
            self._refresh_pages()
            self._refresh_library()
            self.set_status("Last page removed from the PDF. Restore brings it back." if removed else "There is no page to undo.")
        except Exception as error:
            self._error("Undo failed", error)

    def restore_last(self) -> None:
        if self._blocked() or self.capture_busy:
            return
        if self.auto_watching:
            self.stop_auto_watch()
        try:
            restored = self.session.restore_last()
            self._active_index = len(self.session.pages) - 1
            self._refresh_pages()
            self._refresh_library()
            self.set_status("Page restored to the end of the PDF." if restored else "There is no removed page to restore.")
        except Exception as error:
            self._error("Restore failed", error)

    def _refresh_library(self) -> None:
        try:
            self._library_entries = list_sessions(self.sessions_root)
        except Exception as error:
            self._library_entries = []
            self.set_status(f"This project is ready. The project list could not refresh: {error}", "warning")
        if not any(Path(item["directory"]) == self.session.directory for item in self._library_entries):
            self._library_entries.insert(0, {"directory": str(self.session.directory), "title": self.session.title, "page_count": len(self.pages)})
        self.library.delete(0, "end")
        for index, item in enumerate(self._library_entries):
            self.library.insert("end", f"  {item['title']}  ·  {item['page_count']}")
            if Path(item["directory"]) == self.session.directory:
                self.library.selection_set(index)

    def _library_selected(self, _event) -> None:
        selected = self.library.curselection()
        if selected:
            directory = Path(self._library_entries[selected[0]]["directory"])
            if directory != self.session.directory:
                self._switch_project(directory)

    def _can_switch(self) -> bool:
        if self._blocked() or self.capture_busy or self.region_selector is not None or self.region_selector_job is not None:
            self.set_status("Finish the current capture or job before changing projects.")
            return False
        if self.auto_watching:
            self.stop_auto_watch()
        return True

    def new_project(self) -> None:
        if not self._can_switch():
            return
        title = simpledialog.askstring("New project", "Project name", initialvalue="Untitled capture", parent=self.root)
        if title is None:
            return
        try:
            new = Session.create(self.sessions_root, title.strip() or "Untitled capture")
            self.session.close()
            self.session = new
            self._project_changed()
        except Exception as error:
            self._error("Could not create project", error)

    def open_project(self) -> None:
        if not self._can_switch():
            return
        directory = filedialog.askdirectory(parent=self.root, title="Open Page Capture project", initialdir=str(self.sessions_root), mustexist=True)
        if directory:
            self._switch_project(Path(directory))

    def _switch_project(self, directory: Path) -> None:
        if not self._can_switch():
            self._refresh_library()
            return
        try:
            new = Session.load(directory)
            self.session.close()
            self.session = new
            self._project_changed()
        except Exception as error:
            self._error("Could not open project", error)
            self._refresh_library()

    def _project_changed(self) -> None:
        if self._page_viewer is not None:
            self._page_viewer.close()
        self.session.set_region(None)
        self.capture_box = None
        self.selected_virtual_bounds = None
        self.last_signature = None
        self._active_index = -1
        self.region_text.set("No area selected · F6 to select")
        self._refresh_pages()
        self._refresh_library()
        self._save_preference()
        self.set_status("Project opened. Select a fresh capture area or import pages.")

    def show_project_folder(self) -> None:
        try:
            os.startfile(str(self.session.directory))
        except OSError as error:
            self._error("Could not open the project folder", error)

    def show_exports_folder(self) -> None:
        try:
            self.exports_root.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.exports_root))
        except OSError as error:
            self._error("Could not open the exports folder", error)

    def import_documents(self) -> None:
        if not self._can_switch():
            return
        selected = filedialog.askopenfilenames(parent=self.root, title="Import pages in file order",
                                              filetypes=[("Images and PDFs", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.pdf"), ("All files", "*.*")])
        if not selected:
            return
        paths = [Path(path) for path in selected]
        page_range = None
        if len(paths) == 1 and paths[0].suffix.lower() == ".pdf":
            value = simpledialog.askstring("PDF pages", "Pages to import (for example 2-6). Leave blank for every page.", parent=self.root, initialvalue="")
            if value is None:
                return
            if value.strip():
                match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", value)
                if not match:
                    messagebox.showerror("PDF pages", "Enter a page number or a range such as 2-6.", parent=self.root)
                    return
                start = int(match[1])
                end = int(match[2] or start)
                if start < 1 or end < start:
                    messagebox.showerror("PDF pages", "The range must start at page 1 or later and end at or after its start.", parent=self.root)
                    return
                page_range = (start, end)
        self._run_job("Importing pages", lambda progress: import_files(self.session, paths, cancel=self._job_cancel, progress=progress, page_range=page_range),
                      lambda result: self.set_status(f"Imported {result} new {'page' if result == 1 else 'pages'}. Exact duplicates were skipped."))

    def recognize_text(self) -> None:
        if not self._can_switch():
            return
        if not self.ocr_capability.get("available"):
            messagebox.showinfo("Local text recognition", self.ocr_capability.get("reason", "Windows text recognition is unavailable."), parent=self.root)
            return
        if not self.pages:
            self.set_status("Capture or import pages before recognizing text.")
            return
        language = None if self.language.get() == "Automatic" else self.language.get()
        self._run_job("Recognizing text locally", lambda progress: recognize_pages(self.session, language=language, cancel=self._job_cancel, progress=progress),
                      lambda result: self.set_status(f"Text recognition finished. {result['processed']} pages processed, {result.get('skipped', 0)} cached pages reused."))

    def finish_pdf(self) -> None:
        if not self._can_switch():
            return
        if not self.pages:
            self.set_status("Capture or import a page before exporting.")
            return
        searchable = bool(self.searchable_pdf.get())
        if searchable and not self.ocr_capability.get("available") and not all(page.ocr for page in self.session.pages):
            messagebox.showinfo("Searchable PDF", "Text recognition is unavailable. Turn off Searchable PDF to export the original images.\n\n" + self.ocr_capability.get("reason", ""), parent=self.root)
            return
        self.exports_root.mkdir(parents=True, exist_ok=True)
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", self.session.title).strip(" .") or "Captured pages"
        selected = filedialog.asksaveasfilename(parent=self.root, title="Export PDF", initialdir=str(self.exports_root),
                                               initialfile=filename + ".pdf", defaultextension=".pdf", filetypes=[("PDF document", "*.pdf")])
        if not selected:
            return
        output = Path(selected)
        compact = bool(self.compact_pdf.get())
        language = None if self.language.get() == "Automatic" else self.language.get()

        def operation(progress):
            if searchable and self.ocr_capability.get("available"):
                recognize_pages(self.session, language=language, cancel=self._job_cancel, progress=progress)
            return export_pdf(self.session, output, searchable=searchable, compact=compact, cancel=self._job_cancel, progress=progress)

        def done(result):
            self.set_status(f"Saved {result['pages']}-page PDF · {result['searchable_pages']} searchable pages · {result['path']}")
            messagebox.showinfo("PDF exported", f"{result['pages']} pages exported.\n{result['searchable_pages']} pages include recognized text.\n\n{result['path']}", parent=self.root)

        self._run_job("Creating PDF", operation, done)

    def _set_busy(self, busy: bool) -> None:
        for button in self._action_buttons:
            button.configure(state="disabled" if busy else "normal")
        self.library.configure(state="disabled" if busy else "normal")
        if not busy and not self.ocr_capability.get("available"):
            self.ocr_button.configure(text="Text unavailable")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _run_job(self, name: str, operation: Callable, done: Callable) -> None:
        if self._blocked():
            return
        self._job_cancel = threading.Event()
        self.progress.configure(value=0, maximum=1)
        self.progress_text.set(name)
        self.set_status("Press Stop to cancel between pages. Completed pages remain saved.")
        self._set_busy(True)

        def progress(completed, total, message):
            self._events.put(("progress", (completed, total, message)))

        def worker():
            result = error = None
            try:
                result = operation(progress)
            except Exception as caught:
                error = caught
            self._events.put(("finished", (result, error, done)))

        self._job_thread = threading.Thread(target=worker, name="PageCaptureJob", daemon=True)
        self._job_thread.start()

    def stop_job(self) -> None:
        if self._job_thread is not None:
            self._job_cancel.set()
            self.stop_button.configure(state="disabled")
            self.set_status("Stopping after the current page finishes…")

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                completed, total, message = payload
                self.progress.configure(maximum=max(1, total), value=completed)
                self.progress_text.set(f"{message}  ·  {completed}/{total}")
            elif kind == "finished":
                result, error, done = payload
                self._job_thread = None
                if self._closing:
                    self._finalize_close()
                    return
                self._set_busy(False)
                self._active_index = len(self.session.pages) - 1
                self._refresh_pages()
                self._refresh_library()
                if isinstance(error, JobCancelled):
                    self.progress_text.set("Stopped · completed work remains saved")
                    self.set_status("Job cancelled. Review the saved pages before continuing.")
                elif error is not None:
                    self.progress_text.set("Job needs attention")
                    self._error("Could not finish the job", error)
                else:
                    self.progress_text.set("Finished · all work stays on this computer")
                    self.progress.configure(value=self.progress["maximum"])
                    done(result)
        if not self._closed:
            self._event_job = self.root.after(80, self._drain_events)

    def _process_hotkey_events(self) -> None:
        while True:
            try:
                action, payload = self.hotkey_events.get_nowait()
            except queue.Empty:
                break
            if self._closing:
                continue
            if action == "hotkey_error":
                self.set_status("Global function keys are unavailable. The workspace buttons still work.", "warning")
            elif self._job_thread is None:
                callback = {"select": self.select_region, "undo": self.undo_last, "capture": self.capture_page,
                            "finish": self.finish_pdf, "auto": self.toggle_auto_watch}.get(action)
                if callback:
                    callback()
        if not self._closed:
            self._hotkey_job = self.root.after(75, self._process_hotkey_events)

    def _error(self, title: str, error: Exception) -> None:
        self.show_toolbar()
        self.set_status(f"{title}: {error}", "warning")
        messagebox.showerror(title, str(error), parent=self.root)

    def close(self) -> None:
        if self._closed or self._closing:
            return
        self._closing = True
        if self._page_viewer is not None:
            self._page_viewer.close()
        self.auto_watching = False
        if self.region_selector_job is not None:
            self.root.after_cancel(self.region_selector_job)
            self.region_selector_job = None
        if self.region_selector is not None:
            self.region_selector.destroy()
        if self.manual_capture_job is not None:
            self.root.after_cancel(self.manual_capture_job)
            self.manual_capture_job = None
        self.hotkeys.stop()
        if self._job_thread is not None:
            self._set_busy(True)
            self.stop_job()
            self.show_toolbar()
            self.set_status("Finishing the current page before closing safely…")
        else:
            self._finalize_close()

    def _finalize_close(self) -> None:
        if self._closed:
            return
        self.session.close()
        self._closed = True
        for job in (getattr(self, "_hotkey_job", None), getattr(self, "_event_job", None), self._preview_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        self.root.destroy()


def launch(project_dir: Path | None = None) -> None:
    engine.enable_dpi_awareness()
    root = tk.Tk()
    assets_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets"
    icon = assets_root / "pagecapture.ico"
    if icon.is_file():
        try:
            root.iconbitmap(str(icon))
        except tk.TclError:
            pass
    try:
        PageCaptureApp(root, project_dir=project_dir)
    except Exception:
        root.destroy()
        raise
    root.mainloop()
