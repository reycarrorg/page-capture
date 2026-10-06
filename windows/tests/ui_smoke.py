"""Hidden-widget integration checks using synthetic local content only.

Run: .windows-runtime/Scripts/python.exe windows/tests/ui_smoke.py
This does not launch the application entry point or use computer automation.
All Tk top-levels stay withdrawn. Hotkeys, native dialogs, presentation calls,
screen APIs, clipboard APIs and external-app calls are intercepted.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw
from pypdf import PdfReader

from pagecapture import app as ui
from pagecapture.core import JobCancelled, ProjectLockedError, Session, export_pdf


class FakeHotkeys:
    """No thread or operating-system input reader is started."""

    def __init__(self, events):
        self.events = events
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class HiddenWorkspaceSmoke(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pagecapture-hidden-ui-")
        self.directory = Path(self.temporary.name)
        self.documents = self.directory / "redirected-documents"
        self.local_data = self.directory / "local-app-data"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"LOCALAPPDATA": str(self.local_data)}))
        self.stack.enter_context(patch.object(ui, "documents_directory", return_value=self.documents))
        self.stack.enter_context(patch.object(ui, "ocr_available", return_value={
            "available": False, "languages": [], "reason": "Synthetic test uses cached OCR only."}))
        self.stack.enter_context(patch.object(ui.engine, "HotkeyListener", FakeHotkeys))
        self.stack.enter_context(patch.object(ui.PageCaptureApp, "show_toolbar", return_value=None))
        self.stack.enter_context(patch.object(tk.Tk, "attributes", return_value=None))
        self.stack.enter_context(patch.object(tk.Toplevel, "attributes", return_value=None))
        # Viewer presentation calls are intercepted before they reach Tcl/Win32.
        for kind in (tk.Tk, tk.Toplevel):
            for method in ("deiconify", "lift", "focus_set", "focus_force"):
                self.stack.enter_context(patch.object(kind, method, return_value=None))
        self._original_toplevel_init = tk.Toplevel.__init__

        def hidden_toplevel(window, *args, **kwargs):
            self._original_toplevel_init(window, *args, **kwargs)
            window.withdraw()

        self.stack.enter_context(patch.object(tk.Toplevel, "__init__", hidden_toplevel))
        self.dialogs = {}
        for method in ("askopenfilename", "askopenfilenames", "asksaveasfilename", "askdirectory"):
            self.dialogs[method] = self.stack.enter_context(patch.object(ui.filedialog, method, return_value=""))
        self.stack.enter_context(patch.object(ui.simpledialog, "askstring", return_value=None))
        self.info = self.stack.enter_context(patch.object(ui.messagebox, "showinfo", return_value=None))
        self.errors = self.stack.enter_context(patch.object(ui.messagebox, "showerror", return_value=None))
        # Fail loudly if a future test path reaches capture, input or an external app.
        for method in ("cursor_position", "virtual_screen_bounds", "virtual_screen_rectangle",
                       "monitor_bounds", "position_toplevel_absolute", "enable_dpi_awareness"):
            self.stack.enter_context(patch.object(ui.engine, method, side_effect=AssertionError(f"Forbidden screen/input call: {method}")))
        self.stack.enter_context(patch.object(ui.engine.ImageGrab, "grab", side_effect=AssertionError("Screen capture is forbidden")))
        self.stack.enter_context(patch.object(os, "startfile", side_effect=AssertionError("External application calls are forbidden"), create=True))
        for method in ("clipboard_get", "clipboard_clear", "clipboard_append"):
            self.stack.enter_context(patch.object(tk.Misc, method, side_effect=AssertionError("Clipboard access is forbidden")))
        self.callback_errors = []
        self.root = None
        self.app = None
        self._make_app()

    def tearDown(self):
        try:
            if self.app is not None and not self.app._closed:
                self.app.close()
                if not self.app._closed:
                    self._wait_for(lambda: self.app._closed)
        finally:
            self.stack.close()
            self.temporary.cleanup()

    def _make_app(self, project_dir=None):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.app = ui.PageCaptureApp(self.root, project_dir=project_dir)
        self._pump()

    def _assert_hidden(self):
        self.assertEqual(self.root.state(), "withdrawn")
        for child in self.root.winfo_children():
            if isinstance(child, tk.Toplevel):
                self.assertEqual(child.state(), "withdrawn")

    def _pump(self):
        self._assert_hidden()
        self.root.update_idletasks()
        self.root.update()
        self.assertFalse(self.callback_errors, f"Tk callback failure: {self.callback_errors}")
        if not self.app._closed:
            self._assert_hidden()

    def _wait_for(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("Hidden Tk job did not finish within the timeout")
            self._pump()
            time.sleep(0.01)
        self.assertFalse(self.callback_errors)
        self.assertFalse(self.errors.called, self.errors.call_args_list)

    @staticmethod
    def _image(number):
        image = Image.new("RGB", (320, 480), "white")
        drawing = ImageDraw.Draw(image)
        drawing.rectangle((15, 15, 305, 85), fill=(20 + number * 30, 70, 110))
        drawing.text((25, 110), f"Synthetic page {number}", fill="black")
        drawing.rectangle((25, 150 + number * 5, 290, 250), outline="black", width=2)
        return image

    def _add_pages(self, count=3):
        for number in range(1, count + 1):
            with self._image(number) as image:
                self.assertTrue(self.app._save_page(image))

    def _cache_text(self):
        for number, page in enumerate(self.app.session.pages, 1):
            text = f"Synthetic page {number}"
            self.app.session._store_ocr(page, {
                "version": 1, "engine": "Windows.Media.Ocr", "source_sha256": page.sha256,
                "width": page.width, "height": page.height, "language": "en-US", "text": text,
                "lines": [{"text": text, "words": [
                    {"text": "Synthetic", "x": 25, "y": 110, "width": 85, "height": 15},
                    {"text": "page", "x": 115, "y": 110, "width": 40, "height": 15},
                    {"text": str(number), "x": 160, "y": 110, "width": 15, "height": 15},
                ]}],
            })
        self.app._refresh_pages()

    def test_workspace_viewer_and_project_reopen(self):
        self.assertFalse(self.app.pin_on_top.get())
        self.assertEqual(self.app.sessions_root, self.documents / "Page Capture Sessions")
        self.assertTrue(self.app.hotkeys.started)
        self._add_pages()
        with self._image(1) as duplicate:
            self.assertFalse(self.app._save_page(duplicate))
        self.assertEqual(len(self.app.pages), 3)
        self.app._select_page(0)
        self.app._navigate(1)
        self.assertEqual(self.app._active_index, 1)
        self.assertEqual(self.app.page_text.get(), "Page 2 of 3")
        self.assertTrue(self.app.preview.find_all())
        self.app.undo_last()
        self.assertEqual(len(self.app.pages), 2)
        self.app.restore_last()
        self.assertEqual(len(self.app.pages), 3)
        self._cache_text()
        self.app._select_page(0)
        self.app.open_page_viewer()
        self._pump()
        viewer = self.app._page_viewer
        self.assertEqual(viewer.state(), "withdrawn")
        self.assertEqual(viewer.tabs.index("current"), 0)
        self.assertTrue(viewer._fit)
        self.assertGreater(viewer._scale, 0)
        self.assertLessEqual(viewer._scale, 1)
        viewer.actual_size()
        self.assertFalse(viewer._fit)
        self.assertEqual(viewer._scale, 1)
        viewer.zoom(1.25)
        self.assertEqual(viewer._scale, 1.25)
        self.assertIn("125%", viewer.zoom_label.get())
        self.assertGreater(float(viewer.canvas.cget("scrollregion").split()[3]), 480)
        viewer.fit_page()
        self.assertTrue(viewer._fit)
        self.app.show_recognized_text()
        self._pump()
        self.assertEqual(viewer.tabs.index("current"), 1)
        self.assertEqual(viewer.text.cget("state"), "disabled")
        self.assertEqual(viewer.text.get("1.0", "end-1c"), "Synthetic page 1")
        self.app._select_page(1)
        self.assertEqual(viewer.text.get("1.0", "end-1c"), "Synthetic page 2")
        viewer.close()
        self.assertIsNone(self.app._page_viewer)
        project_dir = self.app.session.directory
        self.app.session.set_region((-200, 10, 100, 250))
        preference = json.loads(self.app.preferences_path.read_text(encoding="utf-8"))
        self.assertEqual(preference["last_project"], str(project_dir))
        hotkeys = self.app.hotkeys
        self.app.close()
        self.assertTrue(hotkeys.stopped)
        self.assertTrue(self.app._closed)
        self._make_app(project_dir)
        self.assertEqual(len(self.app.pages), 3)
        self.assertIsNone(self.app.capture_box)
        self.assertIsNone(self.app.session.region)

    def test_import_and_searchable_export_jobs(self):
        sources = []
        for number in (1, 2, 3):
            path = self.directory / f"synthetic-{number}.png"
            with self._image(number) as image:
                image.save(path)
            sources.append(str(path))
        self.dialogs["askopenfilenames"].return_value = tuple(sources)
        self.app.import_documents()
        self.assertIsNotNone(self.app._job_thread)
        self.assertEqual(self.app.export_button.cget("state"), "disabled")
        self._wait_for(lambda: self.app._job_thread is None)
        self.assertEqual(len(self.app.pages), 3)
        self._cache_text()
        self.app.searchable_pdf.set(True)
        output = self.directory / "exports" / "synthetic-searchable.pdf"
        self.dialogs["asksaveasfilename"].return_value = str(output)
        self.app.finish_pdf()
        self.assertIsNotNone(self.app._job_thread)
        self._wait_for(lambda: self.app._job_thread is None)
        self.assertTrue(output.is_file())
        pdf = PdfReader(str(output))
        self.assertEqual(len(pdf.pages), 3)
        for number, page in enumerate(pdf.pages, 1):
            text = " ".join(page.extract_text().split())
            self.assertIn(f"Synthetic page {number}", text)
        self.assertIn("3 searchable pages", self.app.status_text.get())
        self.assertEqual(self.app.stop_button.cget("state"), "disabled")
        self.assertEqual(self.app.export_button.cget("state"), "normal")
        self.assertTrue(self.info.called)
        self.assertEqual(len(self.app.session.pages), 3)

    def test_close_waits_for_cancelled_worker_and_releases_lock(self):
        self._add_pages(1)
        project_dir = self.app.session.directory
        output = self.directory / "cancelled.pdf"
        started = threading.Event()
        cancellation_seen = threading.Event()
        release_worker = threading.Event()

        def operation(progress):
            started.set()
            if not self.app._job_cancel.wait(5):
                raise AssertionError("Close did not request cancellation")
            cancellation_seen.set()
            if not release_worker.wait(5):
                raise AssertionError("Test did not release the cancellation gate")
            return export_pdf(self.app.session, output, searchable=False,
                              cancel=self.app._job_cancel, progress=progress)

        self.app._run_job("Synthetic close check", operation, Mock())
        self.assertTrue(started.wait(2))
        hotkeys = self.app.hotkeys
        self.app.close()
        self.assertTrue(cancellation_seen.wait(2))
        self.assertTrue(self.app._closing)
        self.assertFalse(self.app._closed)
        self.assertTrue(hotkeys.stopped)
        with self.assertRaises(ProjectLockedError):
            Session.load(project_dir)
        release_worker.set()
        self._wait_for(lambda: self.app._closed)
        self.assertFalse(output.exists())
        with Session.load(project_dir) as reopened:
            self.assertEqual(len(reopened.pages), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
