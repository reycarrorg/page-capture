"""Synthetic persistence and output failure checks; no captured user fixtures."""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from pagecapture.core import (JobCancelled, MANIFEST_NAME, ProjectLockedError, Session,
                              export_pdf, import_files, list_sessions, recognize_pages)


class SyntheticRecognizer:
    def __init__(self, language=None):
        self.language = language or "en-US"

    def recognize(self, image):
        return {"text": "café Ω test", "text_angle": None, "lines": [{"text": "café Ω test", "words": [
            {"text": "café", "x": 10, "y": 12, "width": 45, "height": 20},
            {"text": "Ω", "x": 60, "y": 12, "width": 18, "height": 20},
            {"text": "test", "x": 84, "y": 12, "width": 35, "height": 20},
        ]}]}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.session = Session.create(self.root / "projects", "Synthetic pages")

    def tearDown(self):
        self.session.close()
        self.temporary.cleanup()

    def add_page(self, color="white"):
        with Image.new("RGB", (180, 100), color) as image:
            return self.session.add_image(image)

    def make_pdf(self, path, count=3):
        document = canvas.Canvas(str(path), pagesize=(144, 72))
        for number in range(1, count + 1):
            document.setFillColorRGB(number / (count + 1), 0.2, 0.5)
            document.rect(0, 0, 144, 72, fill=1)
            document.setFillColorRGB(1, 1, 1)
            document.drawString(10, 25, f"Synthetic PDF page {number}")
            document.showPage()
        document.save()

    def test_exact_duplicate_and_recoverable_undo_survive_reopen(self):
        first = self.add_page()
        with Image.new("RGB", (180, 100), "white") as image:
            image.info["comment"] = "Synthetic metadata only"
            self.assertIsNone(self.session.add_image(image, "different-name.png"))
        second = self.add_page("navy")
        self.session.set_region((-200, 25, 100, 250))
        saved_image = self.session.page_path(second).read_bytes()
        self.assertEqual(self.session.undo_last().id, second.id)
        self.assertEqual(self.session.page_path(second).read_bytes(), saved_image)
        self.session.close()
        self.session = Session.load(self.session.directory)
        self.assertEqual([page.id for page in self.session.pages], [first.id])
        self.assertEqual(self.session.region, (-200, 25, 100, 250))
        self.assertEqual(self.session.restore_last().id, second.id)
        self.assertEqual(self.session.page_path(second).read_bytes(), saved_image)
        self.assertIsNone(self.session.restore_last())

    def test_other_process_cannot_edit_locked_project_and_release_reopens(self):
        program = (
            "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
            "from pagecapture.core import Session, ProjectLockedError; "
            "exec('try:\\n Session.load(Path(sys.argv[2]))\\nexcept ProjectLockedError:\\n sys.exit(23)')"
        )
        result = subprocess.run([sys.executable, "-c", program,
                                 str(Path(__file__).resolve().parents[1]), str(self.session.directory)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 23, result.stderr)
        with self.assertRaises(ProjectLockedError):
            Session.load(self.session.directory)
        self.session.close()
        self.session = Session.load(self.session.directory)
        self.add_page()

    def test_manifest_replacement_failure_preserves_previous_state_and_new_image(self):
        self.add_page()
        manifest = self.session.directory / MANIFEST_NAME
        previous = manifest.read_bytes()
        original_replace = __import__("os").replace

        def fail_manifest(source, destination):
            if Path(destination) == manifest:
                raise PermissionError("Synthetic manifest write failure")
            return original_replace(source, destination)

        with patch("pagecapture.core.os.replace", side_effect=fail_manifest):
            with self.assertRaises(PermissionError):
                self.add_page("navy")
            with self.assertRaises(PermissionError):
                self.session.set_region((0, 0, 100, 100))
        self.assertEqual(manifest.read_bytes(), previous)
        self.assertEqual(len(self.session.pages), 1)
        self.assertIsNone(self.session.region)
        self.assertEqual(len(list((self.session.directory / "pages").glob("*.png"))), 2)
        self.assertEqual(list(self.session.directory.rglob("*.tmp")), [])
        self.session.close()
        self.session = Session.load(self.session.directory)
        self.assertEqual(len(self.session.pages), 1)

    def test_changed_source_fails_before_ocr_and_preserves_existing_pdf(self):
        page = self.add_page()
        destination = self.root / "existing.pdf"
        destination.write_bytes(b"preserve the complete existing file")
        self.session.page_path(page).write_bytes(b"synthetic damaged original")
        with patch("pagecapture.ocr._Recognizer") as recognizer:
            with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                recognize_pages(self.session)
            recognizer.assert_not_called()
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            export_pdf(self.session, destination)
        self.assertEqual(destination.read_bytes(), b"preserve the complete existing file")
        self.assertEqual(list(self.root.glob("*.building.pdf")), [])

    def test_project_page_traversal_is_rejected_and_failed_load_releases_lock(self):
        self.add_page()
        directory = self.session.directory
        manifest = directory / MANIFEST_NAME
        data = json.loads(manifest.read_text(encoding="utf-8"))
        original = manifest.read_bytes()
        self.session.close()
        data["pages"][0]["filename"] = "../../outside.png"
        manifest.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Page files must"):
            Session.load(directory)
        manifest.write_bytes(original)
        self.session = Session.load(directory)

    def test_import_pdf_range_preserves_source_and_can_resume_after_cancel(self):
        source = self.root / "synthetic.pdf"
        self.make_pdf(source)
        original = source.read_bytes()
        cancelled = threading.Event()

        def stop_after_first(completed, _total, _message):
            if completed == 1:
                cancelled.set()

        with self.assertRaises(JobCancelled):
            import_files(self.session, [source], page_range=(2, 3), cancel=cancelled,
                         progress=stop_after_first)
        self.assertEqual(len(self.session.pages), 1)
        self.assertEqual(import_files(self.session, [source], page_range=(2, 3)), 1)
        self.assertEqual(len(self.session.pages), 2)
        self.assertEqual(self.session.pages[0].width, 288)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(import_files(self.session, [source], page_range=(2, 3)), 0)

    def test_invalid_import_and_range_raise_without_skipping(self):
        bad = self.root / "corrupt.png"
        bad.write_bytes(b"not an image")
        with self.assertRaisesRegex(ValueError, "Could not import corrupt.png"):
            import_files(self.session, [bad])
        source = self.root / "synthetic.pdf"
        self.make_pdf(source)
        for invalid in ((0, 2), (3, 2), (1, 4)):
            with self.assertRaisesRegex(ValueError, "page range"):
                import_files(self.session, [source], page_range=invalid)
        self.assertEqual(self.session.pages, [])

    def test_multiframe_image_import_is_ordered_and_original_is_unchanged(self):
        source = self.root / "synthetic.tiff"
        first = Image.new("RGB", (180, 100), "white")
        second = Image.new("RGB", (180, 100), "navy")
        first.save(source, save_all=True, append_images=[second])
        first.close()
        second.close()
        original = source.read_bytes()
        self.assertEqual(import_files(self.session, [source]), 2)
        with Image.open(self.session.page_paths[0]) as image:
            self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
        with Image.open(self.session.page_paths[1]) as image:
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 128))
        self.assertEqual(source.read_bytes(), original)

    def test_cancelled_ocr_commits_completed_cache_and_resume_skips_it(self):
        self.add_page()
        self.add_page("navy")
        cancelled = threading.Event()

        def stop_after_first(completed, _total, _message):
            if completed == 1:
                cancelled.set()

        with patch("pagecapture.ocr._Recognizer", SyntheticRecognizer):
            with self.assertRaises(JobCancelled):
                recognize_pages(self.session, cancel=cancelled, progress=stop_after_first)
            self.assertIsNotNone(self.session.pages[0].ocr)
            self.assertIsNone(self.session.pages[1].ocr)
            result = recognize_pages(self.session)
            self.assertEqual(result, {"processed": 1, "total": 2, "skipped": 1})
        self.session.close()
        self.session = Session.load(self.session.directory)
        with patch("pagecapture.ocr._Recognizer") as recognizer:
            self.assertEqual(recognize_pages(self.session), {"processed": 0, "total": 2, "skipped": 2})
            recognizer.assert_not_called()

    def test_searchable_pdf_has_unicode_invisible_text_and_honest_page_counts(self):
        self.add_page()
        with patch("pagecapture.ocr._Recognizer", SyntheticRecognizer):
            recognize_pages(self.session)
        self.add_page("navy")
        destination = self.root / "searchable.pdf"
        result = export_pdf(self.session, destination)
        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["searchable_pages"], 1)
        document = PdfReader(destination)
        text = document.pages[0].extract_text()
        self.assertIn("café", text)
        self.assertIn("Ω", text)
        self.assertIn("test", text)
        self.assertIn(b"3 Tr", document.pages[0].get_contents().get_data())
        self.assertEqual(document.pages[1].extract_text(), "")
        self.assertEqual(tuple(float(value) for value in document.pages[0].mediabox), (0, 0, 90, 50))

    def test_stale_or_malformed_ocr_never_adds_searchable_text(self):
        self.add_page()
        with patch("pagecapture.ocr._Recognizer", SyntheticRecognizer):
            recognize_pages(self.session)
        page = self.session.pages[0]
        stale = dict(page.ocr, source_sha256="0" * 64)
        self.session._store_ocr(page, stale)
        result = export_pdf(self.session, self.root / "stale.pdf")
        self.assertEqual(result["searchable_pages"], 0)
        self.assertEqual(PdfReader(self.root / "stale.pdf").pages[0].extract_text(), "")
        malformed = dict(page.ocr, lines=[{"words": [{"text": "test", "x": float("nan")}]}])
        # Export also rejects a manually damaged in-memory cache without passing NaNs to PDF code.
        self.session.pages[0].ocr = malformed
        result = export_pdf(self.session, self.root / "malformed.pdf")
        self.assertEqual(result["searchable_pages"], 0)

    def test_export_cancellation_preserves_destination_and_removes_partial_file(self):
        self.add_page()
        self.add_page("navy")
        destination = self.root / "existing.pdf"
        destination.write_bytes(b"complete previous output")
        cancelled = threading.Event()

        def stop_after_first(completed, _total, _message):
            if completed == 1:
                cancelled.set()

        with self.assertRaises(JobCancelled):
            export_pdf(self.session, destination, cancel=cancelled, progress=stop_after_first)
        self.assertEqual(destination.read_bytes(), b"complete previous output")
        self.assertEqual(list(self.root.glob("*.building.pdf")), [])

    def test_output_replacement_failure_preserves_existing_file(self):
        self.add_page()
        destination = self.root / "existing.pdf"
        destination.write_bytes(b"complete previous output")
        with patch("pagecapture.core.os.replace", side_effect=PermissionError("Synthetic output failure")):
            with self.assertRaises(PermissionError):
                export_pdf(self.session, destination)
        self.assertEqual(destination.read_bytes(), b"complete previous output")
        self.assertEqual(list(self.root.glob("*.building.pdf")), [])

    def test_compact_pdf_preserves_original_and_project_files_are_protected(self):
        page = self.add_page("navy")
        original = self.session.page_path(page).read_bytes()
        destination = self.root / "compact.pdf"
        result = export_pdf(self.session, destination, compact=True, searchable=False)
        self.assertEqual(result["searchable_pages"], 0)
        self.assertEqual(len(PdfReader(destination).pages), 1)
        self.assertEqual(self.session.page_path(page).read_bytes(), original)
        for protected in (self.session.page_path(page), self.session.directory / MANIFEST_NAME):
            with self.assertRaisesRegex(ValueError, "does not replace a project file"):
                export_pdf(self.session, protected)
        self.assertEqual(hashlib.sha256(original).hexdigest(), page.sha256)

    def test_library_uses_metadata_even_if_source_is_missing(self):
        page = self.add_page()
        self.session.page_path(page).unlink()
        with patch("pagecapture.core.Image.open", side_effect=AssertionError("Library must not decode pages")):
            entries = list_sessions(self.root / "projects")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Synthetic pages")
        self.assertEqual(entries[0]["page_count"], 1)
        self.assertTrue(entries[0]["updated_at"])


if __name__ == "__main__":
    unittest.main()
