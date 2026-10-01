import sys
from pathlib import Path
import tempfile
import unittest

windows_legacy_dir = Path(__file__).resolve().parent.parent / "legacy" / "windows"
sys.path.insert(0, str(windows_legacy_dir))

import page_capture

class TestCreateImagePdfEmpty(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_empty_input_compact_false(self):
        output_path = Path(self.temp_dir.name) / "absent_child_dir" / "output.pdf"

        with self.assertRaisesRegex(ValueError, "^No captured pages are available.$"):
            page_capture.create_image_pdf([], output_path, compact=False)

        child_dir = Path(self.temp_dir.name) / "absent_child_dir"
        self.assertFalse(child_dir.exists())
        self.assertEqual(len(list(Path(self.temp_dir.name).iterdir())), 0)

    def test_empty_input_compact_true(self):
        output_path = Path(self.temp_dir.name) / "absent_child_dir" / "output.pdf"

        with self.assertRaisesRegex(ValueError, "^No captured pages are available.$"):
            page_capture.create_image_pdf([], output_path, compact=True)

        child_dir = Path(self.temp_dir.name) / "absent_child_dir"
        self.assertFalse(child_dir.exists())
        self.assertEqual(len(list(Path(self.temp_dir.name).iterdir())), 0)

if __name__ == '__main__':
    unittest.main()
