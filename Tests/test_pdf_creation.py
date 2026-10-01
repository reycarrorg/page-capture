import unittest
import tempfile
import sys
import hashlib
import re
from pathlib import Path
from unittest import mock

from PIL import Image

sys.path.append(str(Path("legacy/windows").resolve()))
from page_capture import create_image_pdf

def get_file_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

class TestPDFCreation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.output_pdf = self.workspace / "output.pdf"

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_synthetic_images(self):
        images = [
            ("RGB", (800, 600), (255, 0, 0)),
            ("L", (1024, 768), 128),
            ("P", (400, 400), 1),
            ("RGBA", (640, 480), (0, 255, 0, 128)),
        ]

        paths = []
        hashes = []
        for i, (mode, size, color) in enumerate(images):
            img = Image.new(mode, size, color=color)
            if mode == "P":
                img.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
            path = self.workspace / f"img_{i}_{mode}.png"
            img.save(path, format="PNG")
            paths.append(path)
            hashes.append(get_file_hash(path))

        return paths, hashes, images

    def test_pdf_creation_real_output(self):
        paths, original_hashes, images_info = self.create_synthetic_images()

        create_image_pdf(paths, self.output_pdf, compact=False)
        self.assertTrue(self.output_pdf.exists())

        with open(self.output_pdf, 'rb') as f:
            pdf_bytes = f.read()

        self.assertTrue(pdf_bytes.startswith(b'%PDF-'))
        self.assertTrue(b'%%EOF' in pdf_bytes)

        pdf_str = pdf_bytes.decode('latin-1')

        for mode, size, color in images_info:
            width, height = size
            self.assertTrue(f"/Width {width}" in pdf_str)
            self.assertTrue(f"/Height {height}" in pdf_str)
            if mode == "L":
                self.assertTrue("/ColorSpace /DeviceGray" in pdf_str)
            elif mode in ("RGB", "RGBA"):
                # ReportLab converts RGBA to RGB in this context or includes it as DeviceRGB
                self.assertTrue("/ColorSpace /DeviceRGB" in pdf_str)

        new_hashes = [get_file_hash(p) for p in paths]
        self.assertEqual(original_hashes, new_hashes)

    def test_pdf_creation_compact_output(self):
        paths, original_hashes, images_info = self.create_synthetic_images()

        create_image_pdf(paths, self.output_pdf, compact=True)
        self.assertTrue(self.output_pdf.exists())

        with open(self.output_pdf, 'rb') as f:
            pdf_bytes = f.read()

        self.assertTrue(pdf_bytes.startswith(b'%PDF-'))
        self.assertTrue(b'%%EOF' in pdf_bytes)

        pdf_str = pdf_bytes.decode('latin-1')

        for mode, size, color in images_info:
            width, height = size
            self.assertTrue(f"/Width {width}" in pdf_str)
            self.assertTrue(f"/Height {height}" in pdf_str)

        new_hashes = [get_file_hash(p) for p in paths]
        self.assertEqual(original_hashes, new_hashes)

    def test_pdf_creation_atomic_error(self):
        paths, original_hashes, _ = self.create_synthetic_images()

        with mock.patch('page_capture.os.replace') as mock_replace:
            mock_replace.side_effect = OSError("Simulated failure")

            with self.assertRaises(OSError):
                create_image_pdf(paths, self.output_pdf, compact=False)

            pdf_files = list(self.workspace.glob("*.pdf"))
            self.assertEqual(len(pdf_files), 0)

if __name__ == '__main__':
    unittest.main()
