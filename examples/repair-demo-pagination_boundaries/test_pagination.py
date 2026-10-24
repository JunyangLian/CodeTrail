import unittest
from pagination import paginate

class PaginationTests(unittest.TestCase):
    def test_first_page(self):
        self.assertEqual(paginate([1,2,3,4,5]), {'items':[1,2,3], 'total_pages':2, 'has_next':True})
    def test_second_page(self):
        self.assertEqual(paginate(list(range(7)), 2, 3)['items'], [3,4,5])
    def test_last_partial_page(self):
        self.assertEqual(paginate(list(range(7)), 3, 3), {'items':[6], 'total_pages':3, 'has_next':False})
    def test_exact_boundary_no_next(self):
        self.assertEqual(paginate([1,2,3], 1, 3), {'items':[1,2,3], 'total_pages':1, 'has_next':False})
    def test_page_beyond_end(self):
        self.assertEqual(paginate([1,2,3,4], 4, 2), {'items':[], 'total_pages':2, 'has_next':False})
    def test_empty(self):
        self.assertEqual(paginate([]), {'items':[], 'total_pages':0, 'has_next':False})
    def test_invalid_page(self):
        for value in (0,-1,True,1.5):
            with self.subTest(value=value), self.assertRaises(ValueError): paginate([1],value,2)
    def test_invalid_size(self):
        for value in (0,-1,True,1.5):
            with self.subTest(value=value), self.assertRaises(ValueError): paginate([1],1,value)

if __name__ == '__main__': unittest.main()
