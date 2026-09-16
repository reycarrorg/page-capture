from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
from io import BytesIO
import os
from pathlib import Path
import queue
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageFont, ImageGrab, ImageTk
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas


APP_NAME = "Page Capture"
CAPTURE_HOTKEY = "F8"
AUTO_HOTKEY = "F10"
DPI_MODE = "unknown"


def enable_dpi_awareness() -> str:
    """Keep capture coordinates accurate on mixed-DPI Windows displays."""
    global DPI_MODE
    try:
        # PER_MONITOR_AWARE_V2 keeps Tk, Win32 cursor coordinates, monitor
        # rectangles, and Pillow capture boxes in the same physical-pixel space.
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-4)
        ):
            DPI_MODE = "per-monitor-v2"
            return DPI_MODE
    except Exception:
        pass
    try:
        result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
        if result == 0:
            DPI_MODE = "per-monitor-v1"
            return DPI_MODE
    except Exception:
        try:
            if ctypes.windll.user32.SetProcessDPIAware():
                DPI_MODE = "system-aware"
        except Exception:
            pass
    return DPI_MODE


def virtual_screen_bounds() -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
    height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
    return left, top, width, height


def virtual_screen_rectangle() -> tuple[int, int, int, int]:
    left, top, width, height = virtual_screen_bounds()
    return left, top, left + width, top + height


def monitor_bounds() -> list[tuple[int, int, int, int]]:
    """Return every active monitor in virtual-screen physical coordinates."""
    monitors: list[tuple[int, int, int, int]] = []
    monitor_callback = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HANDLE,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    @monitor_callback
    def collect_monitor(
        _monitor: int,
        _device_context: int,
        rectangle: ctypes.POINTER(wintypes.RECT),
        _data: int,
    ) -> bool:
        bounds = rectangle.contents
        if bounds.right > bounds.left and bounds.bottom > bounds.top:
            monitors.append(
                (bounds.left, bounds.top, bounds.right, bounds.bottom)
            )
        return True

    try:
        ctypes.windll.user32.EnumDisplayMonitors(
            None, None, collect_monitor, 0
        )
    except Exception:
        monitors.clear()

    if not monitors:
        left, top, width, height = virtual_screen_bounds()
        monitors.append((left, top, left + width, top + height))

    return sorted(monitors, key=lambda bounds: (bounds[1], bounds[0]))


def cursor_position() -> tuple[int, int]:
    point = wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        raise OSError("Windows could not read the mouse position.")
    return point.x, point.y


def local_selection_rectangle(
    selection: tuple[int, int, int, int],
    monitor: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    """Clip a virtual-desktop selection to one monitor and make it local."""
    left = max(selection[0], monitor[0])
    top = max(selection[1], monitor[1])
    right = min(selection[2], monitor[2])
    bottom = min(selection[3], monitor[3])
    if right <= left or bottom <= top:
        return None
    return (
        left - monitor[0],
        top - monitor[1],
        right - monitor[0],
        bottom - monitor[1],
    )


def native_toplevel_handle(window: tk.Toplevel) -> int:
    """Return Tk's native wrapper HWND rather than its small child HWND."""
    user32 = ctypes.windll.user32
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    child = wintypes.HWND(window.winfo_id())
    wrapper = user32.GetParent(child)
    return int(wrapper or child.value)


def position_toplevel_absolute(
    window: tk.Toplevel,
    bounds: tuple[int, int, int, int],
    *,
    show: bool = True,
) -> None:
    """Position a Tk window with Win32 absolute virtual-screen coordinates."""
    left, top, right, bottom = bounds
    width = right - left
    height = bottom - top
    hwnd = native_toplevel_handle(window)
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    flags = 0x0010  # SWP_NOACTIVATE
    if show:
        flags |= 0x0040  # SWP_SHOWWINDOW
    if not user32.SetWindowPos(
        wintypes.HWND(hwnd),
        wintypes.HWND(-1),  # HWND_TOPMOST
        left,
        top,
        width,
        height,
        flags,
    ):
        raise OSError(
            f"Windows could not position the selector at "
            f"({left}, {top}, {width}, {height})."
        )


def frame_signature(image: Image.Image) -> tuple[int, ...]:
    # A 128 x 128 signature remains inexpensive while retaining small text changes,
    # such as a page number or the last few lines of a document page.
    sample = image.convert("L").resize((128, 128), Image.Resampling.BILINEAR)
    if hasattr(sample, "get_flattened_data"):
        return tuple(sample.get_flattened_data())
    return tuple(sample.getdata())


def signature_difference(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def create_image_pdf(
    image_paths: list[Path], output_path: Path, compact: bool = False
) -> None:
    if not image_paths:
        raise ValueError("No captured pages are available.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    first = Image.open(image_paths[0])
    first_width, first_height = first.size
    first.close()

    temp_handle, temp_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}-",
        suffix=".building.pdf",
        dir=output_path.parent,
    )
    os.close(temp_handle)
    temp_path = Path(temp_name)

    try:
        # One PDF point per two image pixels stores the pages at an effective 144 DPI.
        document = pdf_canvas.Canvas(
            str(temp_path),
            pagesize=(first_width / 2, first_height / 2),
            pageCompression=1,
        )
        document.setTitle(output_path.stem)
        document.setAuthor(APP_NAME)
        document.setSubject("Screen pages captured in reading order")

        for image_path in image_paths:
            with Image.open(image_path) as image:
                width, height = image.size
                page_width, page_height = width / 2, height / 2
                document.setPageSize((page_width, page_height))

                if compact:
                    buffer = BytesIO()
                    image.convert("RGB").save(
                        buffer,
                        format="JPEG",
                        quality=92,
                        optimize=True,
                        subsampling=0,
                    )
                    buffer.seek(0)
                    source = ImageReader(buffer)
                else:
                    source = ImageReader(image.copy())

                document.drawImage(
                    source,
                    0,
                    0,
                    width=page_width,
                    height=page_height,
                    preserveAspectRatio=True,
                    anchor="c",
                )
                document.setPageCompression(1)
                document.showPage()

        document.save()
        os.replace(temp_path, output_path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class HotkeyListener:
    POLL_SECONDS = 0.025
    HOTKEYS = {
        0x75: "select",  # F6
        0x76: "undo",  # F7
        0x77: "capture",  # F8
        0x78: "finish",  # F9
        0x79: "auto",  # F10
    }

    def __init__(self, events: queue.Queue[tuple[str, object]]) -> None:
        self.events = events
        self.ready = threading.Event()
        self.stop_requested = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _key_is_pressed(self, virtual_key: int) -> bool:
        return bool(
            ctypes.windll.user32.GetAsyncKeyState(virtual_key) & 0x8000
        )

    def _poll_once(self, previous: dict[int, bool]) -> None:
        for virtual_key, action in self.HOTKEYS.items():
            pressed = self._key_is_pressed(virtual_key)
            if pressed and not previous[virtual_key]:
                self.events.put((action, None))
            previous[virtual_key] = pressed

    def _run(self) -> None:
        try:
            # Seed with the current state so launching while a key is already held
            # does not trigger an unintended action.
            previous = {
                virtual_key: self._key_is_pressed(virtual_key)
                for virtual_key in self.HOTKEYS
            }
        except Exception as error:
            self.events.put(("hotkey_error", str(error)))
            self.ready.set()
            return

        self.ready.set()
        while not self.stop_requested.wait(self.POLL_SECONDS):
            try:
                self._poll_once(previous)
            except Exception as error:
                self.events.put(("hotkey_error", str(error)))
                return

    def stop(self) -> None:
        self.stop_requested.set()
        self.ready.wait(timeout=0.5)
        if self.thread.is_alive():
            self.thread.join(timeout=0.75)


class MonitorOverlay(tk.Toplevel):
    """A monitor-sized piece of the coordinated extended-desktop selector."""

    def __init__(
        self,
        selector: "RegionSelector",
        bounds: tuple[int, int, int, int],
    ) -> None:
        super().__init__(selector.owner.root)
        self.withdraw()
        self.selector = selector
        self.bounds = bounds
        left, top, right, bottom = bounds
        width = right - left
        height = bottom - top

        # Give Tk only the size. Win32 applies the signed absolute position below;
        # Tk otherwise treats a negative coordinate as a bottom/right offset.
        self.geometry(f"{width}x{height}+0+0")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.0)
        self.configure(cursor="crosshair")

        self.canvas = tk.Canvas(self, background="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            width // 2,
            42,
            text="Drag around the page, even across screens.  Esc cancels.",
            fill="white",
            font=("Segoe UI", 18, "bold"),
        )
        self.rectangle = self.canvas.create_rectangle(
            0,
            0,
            0,
            0,
            outline="#38bdf8",
            width=4,
            fill="#38bdf8",
            stipple="gray25",
            state="hidden",
        )

        self.bind("<ButtonPress-1>", lambda _event: selector.begin_drag(self))
        self.bind("<ButtonRelease-1>", lambda _event: selector.finish_drag())
        self.bind("<Escape>", lambda _event: selector.cancel())
        self.update_idletasks()
        self.deiconify()
        self.update_idletasks()
        try:
            position_toplevel_absolute(self, bounds)
        except Exception:
            self.destroy()
            raise
        self.attributes("-alpha", 0.28)

    def show_selection(
        self, rectangle: tuple[int, int, int, int] | None
    ) -> None:
        if rectangle is None:
            self.canvas.itemconfigure(self.rectangle, state="hidden")
            return
        self.canvas.coords(self.rectangle, *rectangle)
        self.canvas.itemconfigure(self.rectangle, state="normal")


class RegionSelector:
    """Coordinate one overlay per monitor as a single extended desktop."""

    POLL_MS = 16
    VK_LBUTTON = 0x01

    def __init__(self, owner: "PageCaptureApp") -> None:
        self.owner = owner
        self.start_root: tuple[int, int] | None = None
        self.current_root: tuple[int, int] | None = None
        self.poll_job: str | None = None
        self.active = False
        self.closed = False
        self.desktop_bounds = virtual_screen_rectangle()
        self.overlays: list[MonitorOverlay] = []
        try:
            for bounds in monitor_bounds():
                self.overlays.append(MonitorOverlay(self, bounds))
        except Exception:
            for overlay in self.overlays:
                try:
                    overlay.destroy()
                except tk.TclError:
                    pass
            self.overlays.clear()
            self.closed = True
            raise

        try:
            pointer_x, pointer_y = cursor_position()
            focused = self.overlays[0]
            for overlay in self.overlays:
                left, top, right, bottom = overlay.bounds
                if left <= pointer_x < right and top <= pointer_y < bottom:
                    focused = overlay
                    break
            focused.focus_force()
        except Exception:
            self.destroy()
            raise

    def begin_drag(self, overlay: MonitorOverlay) -> None:
        if self.closed or self.active:
            return
        self.start_root = cursor_position()
        self.current_root = self.start_root
        self.active = True
        overlay.focus_force()
        self._draw_selection()
        self.poll_job = self.owner.root.after(self.POLL_MS, self._poll_drag)

    def _poll_drag(self) -> None:
        self.poll_job = None
        if self.closed or not self.active:
            return
        self.current_root = cursor_position()
        self._draw_selection()
        if ctypes.windll.user32.GetAsyncKeyState(self.VK_LBUTTON) & 0x8000:
            self.poll_job = self.owner.root.after(self.POLL_MS, self._poll_drag)
        else:
            self.finish_drag()

    def _selection_bounds(self) -> tuple[int, int, int, int] | None:
        if self.start_root is None or self.current_root is None:
            return None
        return (
            min(self.start_root[0], self.current_root[0]),
            min(self.start_root[1], self.current_root[1]),
            max(self.start_root[0], self.current_root[0]),
            max(self.start_root[1], self.current_root[1]),
        )

    def _draw_selection(self) -> None:
        selection = self._selection_bounds()
        if selection is None:
            return
        for overlay in self.overlays:
            overlay.show_selection(
                local_selection_rectangle(selection, overlay.bounds)
            )

    def finish_drag(self) -> None:
        if self.closed or not self.active:
            return
        self.current_root = cursor_position()
        self.active = False
        selection = self._selection_bounds()
        if selection is None:
            self.cancel()
            return

        left, top, right, bottom = selection
        if right - left < 80 or bottom - top < 80:
            self.destroy()
            self.owner.show_toolbar()
            self.owner.set_status("Selection was too small. Try again.", "warning")
            return

        if virtual_screen_rectangle() != self.desktop_bounds:
            self.destroy()
            self.owner.show_toolbar()
            self.owner.set_status(
                "The monitor layout changed. Press F6 and select the page again.",
                "warning",
            )
            return

        self.destroy()
        self.owner.set_region((left, top, right, bottom))
        self.owner.show_toolbar()

    def cancel(self) -> None:
        if self.closed:
            return
        self.destroy()
        self.owner.show_toolbar()
        self.owner.set_status("Region selection cancelled.", "normal")

    def destroy(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.active = False
        if self.poll_job is not None:
            try:
                self.owner.root.after_cancel(self.poll_job)
            except tk.TclError:
                pass
            self.poll_job = None
        for overlay in self.overlays:
            try:
                overlay.destroy()
            except tk.TclError:
                pass
        self.overlays.clear()
        self.owner.region_selector = None


class PageCaptureApp:
    DUPLICATE_THRESHOLD = 0.005
    PAGE_CHANGE_THRESHOLD = 0.02
    AUTO_STABILITY_THRESHOLD = 0.01
    AUTO_STABLE_POLLS = 3
    AUTO_POLL_MS = 300

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("510x690")
        self.root.minsize(470, 640)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.capture_box: tuple[int, int, int, int] | None = None
        self.selected_virtual_bounds: tuple[int, int, int, int] | None = None
        self.pages: list[Path] = []
        self.last_signature: tuple[int, ...] | None = None
        self.capture_busy = False
        self.manual_capture_job: str | None = None
        self.region_selector: RegionSelector | None = None
        self.region_selector_job: str | None = None
        self.auto_watching = False
        self.auto_candidate: Image.Image | None = None
        self.auto_candidate_signature: tuple[int, ...] | None = None
        self.auto_candidate_since = 0.0
        self.auto_stable_polls = 0
        self.thumbnail_photos: list[ImageTk.PhotoImage] = []
        self.gallery_cards: list[ttk.Frame] = []
        self.gallery_placeholder: ttk.Label | None = None

        documents = Path.home() / "Documents"
        self.sessions_root = documents / "Page Capture Sessions"
        self.session_dir = self.sessions_root / datetime.now().strftime(
            "%Y-%m-%d_%H%M%S_%f"
        )
        self.session_ready = False
        self.undone_dir = self.session_dir / "Undone"

        self.delay_seconds = tk.DoubleVar(value=0.45)
        self.compact_pdf = tk.BooleanVar(value=False)
        self.sound_enabled = tk.BooleanVar(value=True)
        self.status_text = tk.StringVar(value="Select the page area to begin.")
        self.region_text = tk.StringVar(value="No region selected")
        self.count_text = tk.StringVar(value="0 pages captured")
        self.hotkey_text = tk.StringVar(
            value="F6 select  |  F7 undo  |  F8 capture  |  F9 finish  |  F10 auto"
        )

        self._build_ui()

        self.hotkey_events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.hotkeys = HotkeyListener(self.hotkey_events)
        self.hotkeys.start()
        self.root.after(75, self._process_hotkey_events)
        if DPI_MODE not in ("per-monitor-v2", "per-monitor-v1"):
            self.set_status(
                "Windows could not enable per-monitor scaling support. "
                "Selection may be inaccurate if the monitors use different scaling.",
                "warning",
            )

    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"))
        style.configure("Counter.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 10))

        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Turn pages into a clean, ordered PDF.",
            foreground="#475569",
        ).pack(anchor="w", pady=(0, 12))

        ttk.Button(
            outer, text="1. Select Page Area", command=self.select_region
        ).pack(fill="x", ipady=6)
        ttk.Label(outer, textvariable=self.region_text).pack(anchor="w", pady=(5, 12))

        action_row = ttk.Frame(outer)
        action_row.pack(fill="x")
        ttk.Button(
            action_row, text="Capture Page (F8)", command=self.capture_page
        ).pack(side="left", fill="x", expand=True, ipady=6)
        ttk.Button(action_row, text="Undo (F7)", command=self.undo_last).pack(
            side="left", padx=(8, 0), ipady=6
        )

        self.auto_button = ttk.Button(
            outer, text="Start Automatic Capture (F10)", command=self.toggle_auto_watch
        )
        self.auto_button.pack(fill="x", pady=(8, 0), ipady=6)

        ttk.Label(outer, textvariable=self.count_text, style="Counter.TLabel").pack(
            anchor="w", pady=(14, 6)
        )

        gallery = ttk.LabelFrame(
            outer,
            text="Captured pages — scroll to review",
            padding=(8, 7),
        )
        gallery.pack(fill="both", expand=True)
        self.gallery_canvas = tk.Canvas(
            gallery,
            height=145,
            background="white",
            highlightthickness=0,
        )
        self.gallery_canvas.pack(fill="both", expand=True)
        gallery_scrollbar = ttk.Scrollbar(
            gallery,
            orient="horizontal",
            command=self.gallery_canvas.xview,
        )
        gallery_scrollbar.pack(fill="x", pady=(5, 0))
        self.gallery_canvas.configure(xscrollcommand=gallery_scrollbar.set)
        self.gallery_content = ttk.Frame(self.gallery_canvas)
        self.gallery_window = self.gallery_canvas.create_window(
            (0, 0), window=self.gallery_content, anchor="nw"
        )
        self.gallery_content.bind("<Configure>", self._sync_gallery_scroll)
        self.gallery_canvas.bind("<MouseWheel>", self._scroll_gallery)
        self._show_empty_gallery()

        options = ttk.LabelFrame(outer, text="Capture options", padding=10)
        options.pack(fill="x", pady=(12, 10))

        delay_row = ttk.Frame(options)
        delay_row.pack(fill="x")
        ttk.Label(delay_row, text="Wait after page turn:").pack(side="left")
        ttk.Spinbox(
            delay_row,
            from_=0.0,
            to=2.0,
            increment=0.1,
            width=6,
            textvariable=self.delay_seconds,
        ).pack(side="right")
        ttk.Label(delay_row, text="seconds").pack(side="right", padx=(0, 5))

        ttk.Checkbutton(
            options,
            text="Photo/compact PDF (JPEG; usually smaller for photographs)",
            variable=self.compact_pdf,
        ).pack(anchor="w", pady=(7, 0))
        ttk.Checkbutton(
            options,
            text="Play a sound after each successful capture",
            variable=self.sound_enabled,
        ).pack(anchor="w", pady=(4, 0))

        ttk.Button(outer, text="Finish and Create PDF (F9)", command=self.finish_pdf).pack(
            fill="x", ipady=7
        )
        self.reset_button = ttk.Button(
            outer,
            text="Reset to 0 Pages (keeps exported PDF and PNGs)",
            command=self.reset_session,
            state="disabled",
        )
        self.reset_button.pack(fill="x", pady=(6, 0), ipady=5)

        ttk.Label(
            outer, textvariable=self.hotkey_text, foreground="#475569"
        ).pack(anchor="center", pady=(8, 3))
        ttk.Label(
            outer,
            textvariable=self.status_text,
            style="Status.TLabel",
            wraplength=445,
            justify="left",
        ).pack(fill="x")

    def _process_hotkey_events(self) -> None:
        while True:
            try:
                action, payload = self.hotkey_events.get_nowait()
            except queue.Empty:
                break

            if action == "hotkey_error":
                self.set_status(
                    "Windows could not read the global function keys. "
                    "The on-screen buttons still work."
                    + (f" Details: {payload}" if payload else ""),
                    "warning",
                )
            elif action == "select":
                self.select_region()
            elif action == "undo":
                self.undo_last()
            elif action == "capture":
                self.capture_page()
            elif action == "finish":
                self.finish_pdf()
            elif action == "auto":
                self.toggle_auto_watch()

        self.root.after(75, self._process_hotkey_events)

    def set_status(self, message: str, _kind: str = "normal") -> None:
        self.status_text.set(message)

    def show_toolbar(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)

    def set_region(self, capture_box: tuple[int, int, int, int]) -> None:
        self.capture_box = capture_box
        self.selected_virtual_bounds = virtual_screen_rectangle()
        width = capture_box[2] - capture_box[0]
        height = capture_box[3] - capture_box[1]
        self.region_text.set(
            f"Selected: {width} x {height} pixels at ({capture_box[0]}, {capture_box[1]})"
        )
        self.set_status(
            "Region locked. Turn the page and press F8, or press F10 for automatic capture."
        )

    def select_region(self) -> None:
        if self.capture_busy:
            self.set_status("Wait for the current capture to finish before selecting a new area.")
            return
        if self.region_selector is not None or self.region_selector_job is not None:
            self.set_status("The extended-desktop selector is already open.")
            return
        if self.auto_watching:
            self.stop_auto_watch()
        self.root.withdraw()
        self.region_selector_job = self.root.after(
            150, self._open_region_selector
        )

    def _open_region_selector(self) -> None:
        self.region_selector_job = None
        try:
            self.region_selector = RegionSelector(self)
        except Exception as error:
            if self.region_selector is not None:
                self.region_selector.destroy()
            self.region_selector = None
            self.show_toolbar()
            messagebox.showerror(
                APP_NAME,
                f"The extended-desktop selector could not open:\n\n{error}",
            )

    def _grab_region(
        self, capture_box: tuple[int, int, int, int] | None = None
    ) -> Image.Image:
        selected_box = capture_box or self.capture_box
        if selected_box is None:
            raise ValueError("Select a page area first.")
        current_virtual_bounds = virtual_screen_rectangle()
        if (
            self.selected_virtual_bounds is None
            or current_virtual_bounds != self.selected_virtual_bounds
        ):
            raise RuntimeError(
                "The monitor layout changed after this area was selected. "
                "Press F6 and select the page again."
            )
        if not (
            current_virtual_bounds[0] <= selected_box[0] < selected_box[2]
            <= current_virtual_bounds[2]
            and current_virtual_bounds[1] <= selected_box[1] < selected_box[3]
            <= current_virtual_bounds[3]
        ):
            raise RuntimeError(
                "The selected area is outside the current extended desktop. "
                "Press F6 and select the page again."
            )
        return ImageGrab.grab(bbox=selected_box, all_screens=True)

    def capture_page(self) -> None:
        if self.capture_box is None:
            self.show_toolbar()
            messagebox.showinfo(APP_NAME, "Select the page area first.")
            return
        if self.capture_busy:
            return

        self.capture_busy = True
        was_hidden = self.auto_watching
        if not was_hidden:
            self.root.withdraw()

        try:
            delay_ms = max(0, int(float(self.delay_seconds.get()) * 1000))
        except (ValueError, tk.TclError):
            delay_ms = 450
        capture_box = self.capture_box
        self.manual_capture_job = self.root.after(
            delay_ms, lambda: self._manual_capture_now(was_hidden, capture_box)
        )

    def _manual_capture_now(
        self,
        keep_hidden: bool,
        capture_box: tuple[int, int, int, int] | None,
    ) -> None:
        self.manual_capture_job = None
        try:
            image = self._grab_region(capture_box)
            if not self._save_page(image, skip_duplicate=True):
                self.set_status("Duplicate page skipped.", "warning")
        except Exception as error:
            self.show_toolbar()
            messagebox.showerror(APP_NAME, f"Capture failed:\n\n{error}")
        finally:
            self.capture_busy = False
            if not keep_hidden:
                self.show_toolbar()

    def _save_page(self, image: Image.Image, skip_duplicate: bool) -> bool:
        signature = frame_signature(image)
        if (
            skip_duplicate
            and self.last_signature is not None
            and signature_difference(signature, self.last_signature)
            <= self.DUPLICATE_THRESHOLD
        ):
            return False

        self._ensure_session_directory()
        page_number = len(self.pages) + 1
        destination = self.session_dir / f"page_{page_number:04d}.png"
        if destination.exists():
            raise FileExistsError(
                f"Page file already exists and was not overwritten: {destination}"
            )
        image.save(destination, format="PNG", compress_level=6)
        self.pages.append(destination)
        self.last_signature = signature
        self.reset_button.configure(state="disabled")
        self._update_count()
        self._append_gallery_page(image, page_number)
        self.set_status(f"Captured page {page_number}: {destination.name}")

        if self.sound_enabled.get():
            try:
                import winsound

                winsound.MessageBeep(winsound.MB_OK)
            except Exception:
                pass
        return True

    def _ensure_session_directory(self) -> None:
        if self.session_ready:
            return

        self.sessions_root.mkdir(parents=True, exist_ok=True)
        candidate = self.session_dir
        for attempt in range(100):
            try:
                candidate.mkdir(exist_ok=False)
                self.session_dir = candidate
                self.undone_dir = candidate / "Undone"
                self.session_ready = True
                return
            except FileExistsError:
                candidate = self.sessions_root / (
                    f"{datetime.now().strftime('%Y-%m-%d_%H%M%S_%f')}_{attempt + 1:02d}"
                )
        raise FileExistsError("Could not create a unique Page Capture session folder.")

    def _update_count(self) -> None:
        suffix = "page" if len(self.pages) == 1 else "pages"
        self.count_text.set(f"{len(self.pages)} {suffix} captured")

    def _sync_gallery_scroll(self, _event: tk.Event | None = None) -> None:
        bounds = self.gallery_canvas.bbox("all")
        if bounds is not None:
            self.gallery_canvas.configure(scrollregion=bounds)

    def _scroll_gallery(self, event: tk.Event) -> str:
        direction = -1 if event.delta > 0 else 1
        self.gallery_canvas.xview_scroll(direction * 3, "units")
        return "break"

    def _show_empty_gallery(self) -> None:
        if self.gallery_placeholder is not None:
            return
        self.gallery_placeholder = ttk.Label(
            self.gallery_content,
            text="Captured-page thumbnails will appear here.",
            anchor="center",
        )
        self.gallery_placeholder.grid(row=0, column=0, padx=95, pady=52)

    def _append_gallery_page(self, image: Image.Image, page_number: int) -> None:
        if self.gallery_placeholder is not None:
            self.gallery_placeholder.destroy()
            self.gallery_placeholder = None

        thumbnail = image.copy()
        thumbnail.thumbnail((125, 105), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(thumbnail)
        card = ttk.Frame(
            self.gallery_content,
            padding=4,
            relief="solid",
            borderwidth=1,
        )
        card.grid(row=0, column=page_number - 1, padx=(0, 7), pady=2)
        ttk.Label(card, image=photo).pack()
        ttk.Label(card, text=f"Page {page_number}").pack(pady=(3, 0))
        self.thumbnail_photos.append(photo)
        self.gallery_cards.append(card)
        self._sync_gallery_scroll()
        self.root.after_idle(lambda: self.gallery_canvas.xview_moveto(1.0))

    def _remove_last_gallery_page(self) -> None:
        if self.gallery_cards:
            self.gallery_cards.pop().destroy()
        if self.thumbnail_photos:
            self.thumbnail_photos.pop()
        if not self.gallery_cards:
            self._show_empty_gallery()
        self._sync_gallery_scroll()

    def _clear_gallery(self) -> None:
        for card in self.gallery_cards:
            card.destroy()
        self.gallery_cards.clear()
        self.thumbnail_photos.clear()
        if self.gallery_placeholder is not None:
            self.gallery_placeholder.destroy()
            self.gallery_placeholder = None
        self._show_empty_gallery()
        self._sync_gallery_scroll()
        self.gallery_canvas.xview_moveto(0.0)

    def undo_last(self) -> None:
        if self.capture_busy:
            self.set_status("Wait for the current capture to finish before undoing it.")
            return
        if self.auto_watching:
            self.stop_auto_watch()
        if not self.pages:
            self.set_status("There is no captured page to undo.")
            return

        source = self.pages[-1]
        try:
            self.undone_dir.mkdir(parents=True, exist_ok=True)
            destination = self.undone_dir / (
                f"{source.stem}_{datetime.now().strftime('%H%M%S_%f')}{source.suffix}"
            )
            shutil.move(str(source), str(destination))
        except Exception as error:
            messagebox.showerror(
                APP_NAME,
                "Undo could not safely move the page, so nothing was changed."
                f"\n\n{error}",
            )
            return

        self.pages.pop()
        self.reset_button.configure(state="disabled")

        if self.pages:
            with Image.open(self.pages[-1]) as previous:
                self.last_signature = frame_signature(previous)
        else:
            self.last_signature = None

        self._remove_last_gallery_page()
        self._update_count()
        self.set_status(
            f"Last page moved to the recoverable Undone folder: {destination.name}"
        )

    def toggle_auto_watch(self) -> None:
        if self.capture_busy:
            self.set_status("Wait for the current capture to finish before changing modes.")
            return
        if self.auto_watching:
            self.stop_auto_watch()
        else:
            self.start_auto_watch()

    def start_auto_watch(self) -> None:
        if self.capture_box is None:
            self.show_toolbar()
            messagebox.showinfo(APP_NAME, "Select the page area first.")
            return

        self.auto_watching = True
        self.auto_candidate = None
        self.auto_candidate_signature = None
        self.auto_stable_polls = 0
        self.auto_button.configure(text="Stop Automatic Capture (F10)")
        self.set_status(
            "Best-effort automatic capture is running. Turn pages normally; press F10 to return."
        )
        self.root.withdraw()

        # Capture the page currently visible, then watch for stable changes.
        self.root.after(350, self._auto_capture_initial)

    def _auto_capture_initial(self) -> None:
        if not self.auto_watching:
            return
        try:
            image = self._grab_region()
            self._save_page(image, skip_duplicate=True)
        except Exception as error:
            self.stop_auto_watch()
            messagebox.showerror(APP_NAME, f"Automatic capture failed:\n\n{error}")
            return
        self.root.after(self.AUTO_POLL_MS, self._auto_tick)

    def _auto_tick(self) -> None:
        if not self.auto_watching:
            return

        try:
            image = self._grab_region()
            signature = frame_signature(image)
            if self.last_signature is None:
                self._save_page(image, skip_duplicate=False)
            else:
                changed = signature_difference(signature, self.last_signature)
                if changed >= self.PAGE_CHANGE_THRESHOLD:
                    if self.auto_candidate_signature is None:
                        self.auto_candidate = image
                        self.auto_candidate_signature = signature
                        self.auto_candidate_since = time.monotonic()
                        self.auto_stable_polls = 1
                    else:
                        movement = signature_difference(
                            signature, self.auto_candidate_signature
                        )
                        if movement > self.AUTO_STABILITY_THRESHOLD:
                            self.auto_candidate = image
                            self.auto_candidate_signature = signature
                            self.auto_candidate_since = time.monotonic()
                            self.auto_stable_polls = 1
                        else:
                            # Keep the most recently observed stable frame so the saved
                            # page is not an earlier frame from the page-turn transition.
                            self.auto_candidate = image
                            self.auto_candidate_signature = signature
                            self.auto_stable_polls += 1
                            try:
                                settle_time = max(
                                    0.2, float(self.delay_seconds.get())
                                )
                            except (ValueError, tk.TclError):
                                settle_time = 0.45
                            if (
                                self.auto_stable_polls >= self.AUTO_STABLE_POLLS
                                and time.monotonic() - self.auto_candidate_since
                                >= settle_time
                            ):
                                candidate = self.auto_candidate or image
                                self._save_page(candidate, skip_duplicate=True)
                                self.auto_candidate = None
                                self.auto_candidate_signature = None
                                self.auto_stable_polls = 0
                else:
                    self.auto_candidate = None
                    self.auto_candidate_signature = None
                    self.auto_stable_polls = 0
        except Exception as error:
            self.stop_auto_watch()
            messagebox.showerror(APP_NAME, f"Automatic capture failed:\n\n{error}")
            return

        self.root.after(self.AUTO_POLL_MS, self._auto_tick)

    def stop_auto_watch(self) -> None:
        self.auto_watching = False
        self.auto_candidate = None
        self.auto_candidate_signature = None
        self.auto_stable_polls = 0
        self.auto_button.configure(text="Start Automatic Capture (F10)")
        self.show_toolbar()
        self.set_status("Automatic capture stopped. Review the page count or finish the PDF.")

    def finish_pdf(self) -> None:
        if self.capture_busy:
            self.set_status("Wait for the current capture to finish before creating the PDF.")
            return
        if self.auto_watching:
            self.stop_auto_watch()
        if not self.pages:
            self.show_toolbar()
            messagebox.showinfo(APP_NAME, "Capture at least one page first.")
            return

        default_name = f"captured-pages-{datetime.now().strftime('%Y-%m-%d')}.pdf"
        output = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save captured pages as PDF",
            initialdir=str(self.session_dir),
            initialfile=default_name,
            defaultextension=".pdf",
            filetypes=[("PDF document", "*.pdf")],
        )
        if not output:
            self.set_status("PDF creation cancelled; captured images are still saved.")
            return

        try:
            output_path = Path(output)
            create_image_pdf(self.pages, output_path, compact=self.compact_pdf.get())
        except Exception as error:
            messagebox.showerror(APP_NAME, f"PDF creation failed:\n\n{error}")
            return

        self.reset_button.configure(state="normal")
        self.set_status(
            f"PDF created: {output_path}. Reset to 0 Pages is now available."
        )
        messagebox.showinfo(
            APP_NAME,
            f"Created {len(self.pages)}-page PDF:\n\n{output_path}\n\n"
            f"The original PNG pages remain in:\n{self.session_dir}\n\n"
            "Use Reset to 0 Pages when you are ready to start the next PDF.",
        )

    def reset_session(self) -> None:
        if self.capture_busy:
            self.set_status("Wait for the current capture to finish before resetting.")
            return
        if self.auto_watching:
            self.stop_auto_watch()

        preserved_session = self.session_dir if self.session_ready else None
        self.pages.clear()
        self.last_signature = None
        self.auto_candidate = None
        self.auto_candidate_signature = None
        self.auto_stable_polls = 0
        self.session_dir = self.sessions_root / datetime.now().strftime(
            "%Y-%m-%d_%H%M%S_%f"
        )
        self.session_ready = False
        self.undone_dir = self.session_dir / "Undone"
        self._clear_gallery()
        self._update_count()
        self.reset_button.configure(state="disabled")
        preservation_note = (
            f" Previous PNGs remain in: {preserved_session}."
            if preserved_session is not None
            else ""
        )
        if self.capture_box is None:
            self.set_status(
                "Reset complete: 0 pages. Select a page area for the new PDF."
                + preservation_note
            )
        else:
            self.set_status(
                "Reset complete: 0 pages. The selected area is still ready for F8."
                + preservation_note
            )

    def close(self) -> None:
        if self.auto_watching:
            self.auto_watching = False
        if self.region_selector_job is not None:
            try:
                self.root.after_cancel(self.region_selector_job)
            except tk.TclError:
                pass
            self.region_selector_job = None
        if self.region_selector is not None:
            self.region_selector.destroy()
        if self.manual_capture_job is not None:
            try:
                self.root.after_cancel(self.manual_capture_job)
            except tk.TclError:
                pass
            self.manual_capture_job = None
            self.capture_busy = False
        self.hotkeys.stop()
        self.root.destroy()


def find_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path(os.environ.get("WINDIR", "C:\\Windows"))
        / "Fonts"
        / ("segoeuib.ttf" if bold else "segoeui.ttf"),
        Path(os.environ.get("WINDIR", "C:\\Windows"))
        / "Fonts"
        / ("arialbd.ttf" if bold else "arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def run_self_test(output_path: Path) -> None:
    workspace = Path(__file__).resolve().parents[2]
    scratch = workspace / "tmp" / "pdfs" / "page-capture-self-test"
    scratch.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    page_paths: list[Path] = []
    title_font = find_font(54, bold=True)
    body_font = find_font(30)
    footer_font = find_font(24)

    for page_number in range(1, 4):
        image = Image.new("RGB", (1600, 900), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1600, 120), fill="#0f172a")
        draw.text(
            (70, 31),
            "Page Capture - visual verification",
            fill="white",
            font=title_font,
        )
        draw.text(
            (80, 190),
            f"Sample page {page_number}",
            fill="#0f172a",
            font=title_font,
        )
        paragraphs = [
            "Each screenshot becomes one PDF page in the same order it was captured.",
            "Lossless mode preserves sharp text. Compact mode creates a smaller PDF.",
            "The original PNG pages remain available after the PDF is created.",
        ]
        y = 310
        for paragraph in paragraphs:
            draw.rounded_rectangle(
                (80, y - 18, 1520, y + 66), radius=16, fill="#f1f5f9"
            )
            draw.text((112, y), paragraph, fill="#1e293b", font=body_font)
            y += 125
        draw.text(
            (80, 825),
            f"Verification page {page_number} of 3",
            fill="#475569",
            font=footer_font,
        )

        page_path = scratch / f"page_{page_number:04d}.png"
        image.save(page_path, format="PNG", compress_level=6)
        page_paths.append(page_path)

    with Image.open(page_paths[0]) as first_page, Image.open(page_paths[1]) as second_page:
        first_signature = frame_signature(first_page)
        repeated_signature = frame_signature(first_page.copy())
        second_signature = frame_signature(second_page)
        if signature_difference(first_signature, repeated_signature) > PageCaptureApp.DUPLICATE_THRESHOLD:
            raise AssertionError("An identical page was not recognized as a duplicate.")
        if signature_difference(first_signature, second_signature) <= PageCaptureApp.PAGE_CHANGE_THRESHOLD:
            raise AssertionError("A small page-text change was not detected.")

    create_image_pdf(page_paths, output_path, compact=False)
    print(output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Generate a three-page PDF used to verify the PDF pipeline.",
    )
    parser.add_argument(
        "--self-test-output",
        type=Path,
        default=None,
        help="Destination for the self-test PDF.",
    )
    parser.add_argument(
        "--ui-smoke-test",
        action="store_true",
        help="Build the Windows interface once without leaving it open.",
    )
    parser.add_argument(
        "--runtime-check",
        action="store_true",
        help="Verify required libraries before the launcher opens the interface.",
    )
    arguments = parser.parse_args()

    if arguments.runtime_check:
        if sys.platform != "win32":
            raise SystemExit("Page Capture requires Microsoft Windows.")
        print("RUNTIME_CHECK_OK")
        return

    if arguments.self_test:
        workspace = Path(__file__).resolve().parents[2]
        output = arguments.self_test_output or (
            workspace / "output" / "pdf" / "page-capture-demo.pdf"
        )
        run_self_test(output.resolve())
        return

    if arguments.ui_smoke_test:
        if sys.platform != "win32":
            raise SystemExit("Page Capture requires Microsoft Windows.")
        enable_dpi_awareness()
        root = tk.Tk()
        root.withdraw()
        app = PageCaptureApp(root)
        root.update_idletasks()
        root.update()
        app.close()
        print("UI_SMOKE_TEST_OK")
        return

    if sys.platform != "win32":
        raise SystemExit("Page Capture requires Microsoft Windows.")

    enable_dpi_awareness()
    root = tk.Tk()
    PageCaptureApp(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if sys.platform == "win32":
            try:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    f"Page Capture could not start or complete the action.\n\n{error}",
                    APP_NAME,
                    0x10,
                )
            except Exception:
                pass
        raise
