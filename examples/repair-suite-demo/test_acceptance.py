import unittest
from public_api import render

class RepairTests(unittest.TestCase):
    def test_01(self):
        self.assertEqual(render('2020-01-02T03:04:05.851').microsecond,851000)
    def test_02(self):
        self.assertEqual(render('2020-01-02T03:04:05.051').microsecond,51000)
    def test_03(self):
        self.assertEqual(render('2020-01-02T03:04:05.1').microsecond,100000)
    def test_04(self):
        self.assertEqual(render('2020-01-02T03:04:05.000001').microsecond,1)
    def test_05(self):
        self.assertEqual(render('2020-01-02T03:04:05.123456789').microsecond,123456)
    def test_06(self):
        self.assertEqual(render('2020-01-02T03:04:05').second,5)
    def test_07(self):
        self.assertEqual(render('2020-01-02T03:04:05.000000').microsecond,0)
    def test_08(self):
        with self.assertRaises(ValueError): render('invalid')
