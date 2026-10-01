import sys
import os
from pathlib import Path
import unittest
from unittest.mock import patch

windows_legacy_dir = Path(__file__).resolve().parent.parent / "legacy" / "windows"
sys.path.insert(0, str(windows_legacy_dir))

import page_capture

class TestVirtualScreenRectangle(unittest.TestCase):
    @patch('page_capture.virtual_screen_bounds')
    def test_positive_origin(self, mock_bounds):
        # (left, top, width, height)
        mock_bounds.return_value = (10, 20, 800, 600)
        # Expected: (left, top, left + width, top + height)
        self.assertEqual(page_capture.virtual_screen_rectangle(), (10, 20, 810, 620))

    @patch('page_capture.virtual_screen_bounds')
    def test_negative_origin(self, mock_bounds):
        mock_bounds.return_value = (-1920, -1080, 1920, 1080)
        self.assertEqual(page_capture.virtual_screen_rectangle(), (-1920, -1080, 0, 0))

    @patch('page_capture.virtual_screen_bounds')
    def test_mixed_origin(self, mock_bounds):
        mock_bounds.return_value = (-100, 50, 1024, 768)
        self.assertEqual(page_capture.virtual_screen_rectangle(), (-100, 50, 924, 818))

    @patch('page_capture.virtual_screen_bounds')
    def test_zero_width_height(self, mock_bounds):
        mock_bounds.return_value = (0, 0, 0, 0)
        self.assertEqual(page_capture.virtual_screen_rectangle(), (0, 0, 0, 0))

if __name__ == '__main__':
    unittest.main()
