import sys
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
from datetime import datetime

# Mock Windows dependencies and external libraries for safe import on Linux
sys.modules['PIL'] = MagicMock()
sys.modules['reportlab'] = MagicMock()
sys.modules['reportlab.lib'] = MagicMock()
sys.modules['reportlab.lib.utils'] = MagicMock()
sys.modules['reportlab.pdfgen'] = MagicMock()

import ctypes
if not hasattr(ctypes, 'windll'):
    ctypes.windll = MagicMock()
if not hasattr(ctypes, 'wintypes'):
    ctypes.wintypes = MagicMock()
    for attr in ['BOOL', 'HANDLE', 'HDC', 'RECT', 'LPARAM', 'POINT', 'HWND', 'UINT']:
        setattr(ctypes.wintypes, attr, MagicMock())

from legacy.windows.page_capture import PageCaptureApp

class SyntheticApp:
    def __init__(self):
        self.session_ready = False
        self.sessions_root = Path("/fake/sessions_root")
        self.session_dir = self.sessions_root / "2023-01-01_120000_123456"
        self.undone_dir = self.session_dir / "Undone"

    # Bind the unbound method to this instance
    _ensure_session_directory = PageCaptureApp._ensure_session_directory

class TestSessionDirectory(unittest.TestCase):
    def test_already_ready(self):
        app = SyntheticApp()
        app.session_ready = True

        with patch.object(Path, 'mkdir') as mock_mkdir:
            app._ensure_session_directory()
            mock_mkdir.assert_not_called()
            self.assertTrue(app.session_ready)

    def test_success_after_collision(self):
        app = SyntheticApp()

        original_session_dir = app.session_dir

        # We need Path.mkdir to succeed for exist_ok=True, fail once for exist_ok=False, then succeed.
        def mock_mkdir_side_effect(*args, **kwargs):
            if kwargs.get('exist_ok') is False:
                # We use a stateful closure or just check call count
                if not hasattr(mock_mkdir_side_effect, 'called'):
                    mock_mkdir_side_effect.called = True
                    raise FileExistsError()
            return None

        with patch.object(Path, 'mkdir', side_effect=mock_mkdir_side_effect) as mock_mkdir:
            app._ensure_session_directory()

            # The root should be created (parents=True, exist_ok=True)
            # The first candidate should fail
            # The second candidate should succeed
            self.assertEqual(mock_mkdir.call_count, 3)

            self.assertTrue(app.session_ready)
            self.assertNotEqual(app.session_dir, original_session_dir)
            self.assertEqual(app.undone_dir, app.session_dir / "Undone")

    @patch('legacy.windows.page_capture.datetime')
    def test_failure_fallback(self, mock_datetime):
        # We mock datetime to have deterministic folder names in the fallback
        mock_datetime.now.return_value = datetime(2023, 1, 1, 12, 0, 0, 123456)

        app = SyntheticApp()

        def mock_mkdir_side_effect(*args, **kwargs):
            if kwargs.get('exist_ok') is False:
                raise FileExistsError()
            return None

        with patch.object(Path, 'mkdir', side_effect=mock_mkdir_side_effect) as mock_mkdir:
            with self.assertRaises(FileExistsError) as context:
                app._ensure_session_directory()

            self.assertEqual(str(context.exception), "Could not create a unique Page Capture session folder.")

            # 1 for root creation + 100 attempts
            self.assertEqual(mock_mkdir.call_count, 101)
            self.assertFalse(app.session_ready)
