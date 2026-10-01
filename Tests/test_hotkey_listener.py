import queue
import unittest
from unittest.mock import MagicMock

from legacy.windows.page_capture import HotkeyListener
import legacy.windows.page_capture

class TestHotkeyListener(unittest.TestCase):
    def setUp(self):
        self.events_queue = queue.Queue()
        self.listener = HotkeyListener(self.events_queue)

        # We will mock _key_is_pressed for _poll_once tests
        self.listener._key_is_pressed = MagicMock()

        # Extract hotkeys to have easy access to the keys mapping
        self.hotkeys = HotkeyListener.HOTKEYS

        # The dictionary needs to contain all HOTKEYS to mimic production behavior
        # because the for loop in _poll_once iterates over HOTKEYS
        self.default_previous = {k: False for k in self.hotkeys}

    def test_key_press_from_unpressed_state(self):
        # Setup initial state: F8 (capture) was not pressed
        previous = self.default_previous.copy()

        # Currently, F8 is pressed
        self.listener._key_is_pressed.side_effect = lambda k: True if k == 0x77 else False

        self.listener._poll_once(previous)

        # Check action was queued
        self.assertEqual(self.events_queue.qsize(), 1)
        action, data = self.events_queue.get_nowait()
        self.assertEqual(action, "capture")
        self.assertIsNone(data)

        # Check previous state was updated
        self.assertTrue(previous[0x77])

    def test_key_held_down(self):
        # Setup initial state: F8 (capture) was already pressed
        previous = self.default_previous.copy()
        previous[0x77] = True

        # Currently, F8 is still pressed
        self.listener._key_is_pressed.side_effect = lambda k: True if k == 0x77 else False

        self.listener._poll_once(previous)

        # Check no action was queued
        self.assertEqual(self.events_queue.qsize(), 0)

        # Check previous state is still True
        self.assertTrue(previous[0x77])

    def test_key_release(self):
        # Setup initial state: F8 (capture) was pressed
        previous = self.default_previous.copy()
        previous[0x77] = True

        # Currently, F8 is not pressed
        self.listener._key_is_pressed.side_effect = lambda k: False

        self.listener._poll_once(previous)

        # Check no action was queued
        self.assertEqual(self.events_queue.qsize(), 0)

        # Check previous state was updated
        self.assertFalse(previous[0x77])

    def test_key_remains_unpressed(self):
        # Setup initial state: F8 (capture) was not pressed
        previous = self.default_previous.copy()

        # Currently, F8 is still not pressed
        self.listener._key_is_pressed.side_effect = lambda k: False

        self.listener._poll_once(previous)

        # Check no action was queued
        self.assertEqual(self.events_queue.qsize(), 0)

        # Check previous state is still False
        self.assertFalse(previous[0x77])

    def test_multiple_keys(self):
        # Setup initial state
        previous = self.default_previous.copy()
        previous[0x75] = False # select
        previous[0x76] = True  # undo (held down)
        previous[0x77] = True  # capture (released)
        previous[0x78] = False # finish (pressed)
        previous[0x79] = False # auto

        def mock_is_pressed(k):
            # Currently pressing F7 (held), F8 is released, F9 is newly pressed
            if k == 0x76: return True
            if k == 0x78: return True
            return False

        self.listener._key_is_pressed.side_effect = mock_is_pressed

        self.listener._poll_once(previous)

        # Check queued actions: only 'finish' should be queued (F9 transitioned from False to True)
        self.assertEqual(self.events_queue.qsize(), 1)
        action, data = self.events_queue.get_nowait()
        self.assertEqual(action, "finish")
        self.assertIsNone(data)

        # Check previous states are updated correctly
        self.assertFalse(previous[0x75]) # Still False
        self.assertTrue(previous[0x76])  # Held True
        self.assertFalse(previous[0x77]) # Transitioned False
        self.assertTrue(previous[0x78])  # Transitioned True
        self.assertFalse(previous[0x79]) # Still False

class TestCtypesRestoration(unittest.TestCase):
    def test_ctypes_remains_real(self):
        import ctypes
        self.assertIsNot(legacy.windows.page_capture.ctypes, MagicMock)
        self.assertIs(legacy.windows.page_capture.ctypes, ctypes)

if __name__ == '__main__':
    unittest.main()
