import unittest
from legacy.windows.page_capture import signature_difference

class TestSignatureDifference(unittest.TestCase):
    def test_equal_tuples(self):
        self.assertEqual(signature_difference((1, 2, 3), (1, 2, 3)), 0.0)

    def test_known_nonzero_average_absolute_difference(self):
        # sum of abs(a - b): abs(10-12) + abs(20-16) = 2 + 4 = 6
        # average: 6 / 2 = 3.0
        self.assertEqual(signature_difference((10, 20), (12, 16)), 3.0)

    def test_both_empty(self):
        self.assertEqual(signature_difference((), ()), 255.0)

    def test_one_empty(self):
        self.assertEqual(signature_difference((), (1,)), 255.0)
        self.assertEqual(signature_difference((1,), ()), 255.0)

    def test_unequal_lengths(self):
        self.assertEqual(signature_difference((1,), (1, 2)), 255.0)
        self.assertEqual(signature_difference((1, 2, 3), (1, 2)), 255.0)

if __name__ == '__main__':
    unittest.main()
