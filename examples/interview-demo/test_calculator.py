import unittest

from calculator import add


class CalculatorTests(unittest.TestCase):
    def test_positive_numbers(self):
        self.assertEqual(add(2, 3), 5)

    def test_zero(self):
        self.assertEqual(add(7, 0), 7)

    def test_negative_numbers(self):
        self.assertEqual(add(-1, -2), -3)


if __name__ == "__main__":
    unittest.main()
