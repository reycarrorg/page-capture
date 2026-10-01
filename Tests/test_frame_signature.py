import unittest
from PIL import Image

from legacy.windows.page_capture import frame_signature

class TestFrameSignature(unittest.TestCase):

    def test_real_pil_image_integration(self):
        # Use a small non-square RGB PIL image
        image = Image.new("RGB", (200, 100), color=(255, 0, 0))

        # Test the function
        signature = frame_signature(image)

        # Verify exactly 128x128 (16384) integers are returned
        self.assertIsInstance(signature, tuple)
        self.assertEqual(len(signature), 16384)
        self.assertTrue(all(isinstance(x, int) for x in signature))

    def test_deterministic_pixel_values(self):
        # Create small solid-color images to test deterministic grayscale conversion
        white_image = Image.new("RGB", (10, 10), color=(255, 255, 255))
        black_image = Image.new("RGB", (10, 10), color=(0, 0, 0))
        blue_image = Image.new("RGB", (10, 10), color=(0, 0, 255))

        white_sig = frame_signature(white_image)
        black_sig = frame_signature(black_image)
        blue_sig = frame_signature(blue_image)

        self.assertEqual(len(white_sig), 16384)

        # White should convert to 255 in L mode
        self.assertTrue(all(val == 255 for val in white_sig))

        # Black should convert to 0 in L mode
        self.assertTrue(all(val == 0 for val in black_sig))

        # Blue (0, 0, 255) converts to roughly 29 in L mode (255 * 0.114)
        self.assertTrue(all(val == 29 for val in blue_sig))

    def test_getdata_path_with_test_double(self):
        # Test double that simulates an image that does not have get_flattened_data
        class MockResizedImage:
            def getdata(self):
                return [10, 20, 30]

        class MockConvertedImage:
            def resize(self, size, resample):
                self.size_called = size
                self.resample_called = resample
                return MockResizedImage()

        class MockImage:
            def convert(self, mode):
                self.mode_called = mode
                return MockConvertedImage()

        mock_image = MockImage()
        signature = frame_signature(mock_image)

        self.assertEqual(signature, (10, 20, 30))
        self.assertEqual(mock_image.mode_called, "L")

    def test_get_flattened_data_path_with_test_double(self):
        # Test double that simulates an image that has get_flattened_data
        class MockResizedImage:
            def get_flattened_data(self):
                return [40, 50, 60]

            def getdata(self):
                return [99, 99, 99] # Should not be called

        class MockConvertedImage:
            def resize(self, size, resample):
                return MockResizedImage()

        class MockImage:
            def convert(self, mode):
                return MockConvertedImage()

        mock_image = MockImage()
        signature = frame_signature(mock_image)

        self.assertEqual(signature, (40, 50, 60))

if __name__ == "__main__":
    unittest.main()
