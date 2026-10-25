import unittest
from graph import dependency_order as order

class RepairTests(unittest.TestCase):
    def test_01(self):
        self.assertEqual(order({}), [])
    def test_02(self):
        self.assertEqual(order({'a':['b']}), ['b','a'])
    def test_03(self):
        self.assertEqual(order({'a':['b'],'b':['c']}), ['c','b','a'])
    def test_04(self):
        self.assertEqual(order({'a':[],'b':['a']}), ['a','b'])
    def test_05(self):
        with self.assertRaises(ValueError): order({'a':['a']})
    def test_06(self):
        with self.assertRaises(ValueError): order({'a':['b'],'b':['a']})
    def test_07(self):
        x=order({'a':['c'],'b':['c']})
        self.assertEqual(len(x),3)
        self.assertLess(x.index('c'),x.index('a'))
        self.assertLess(x.index('c'),x.index('b'))
    def test_08(self):
        g={'a':['b']}; order(g)
        self.assertEqual(g,{'a':['b']})
