import copy
import unittest
from repository import get_user
from service import display_name

class LookupTests(unittest.TestCase):
    def setUp(self):
        self.users = {'user-a':{'name':'Ada'}, 'user-b':{'name':'Lin'}}
    def test_direct_repository_normalizes(self):
        self.assertEqual(get_user(self.users, ' USER-A '), {'name':'Ada'})
    def test_service_uses_correct_argument_order(self):
        self.assertEqual(display_name(self.users, 'user-b'), 'Lin')
    def test_service_normalizes(self):
        self.assertEqual(display_name(self.users, ' User-A '), 'Ada')
    def test_unknown_returns_none(self):
        self.assertIsNone(display_name(self.users, 'missing'))
    def test_empty_mapping(self):
        self.assertIsNone(display_name({}, 'user-a'))
    def test_none_id_is_rejected(self):
        with self.assertRaises(ValueError): display_name(self.users, None)
    def test_input_mapping_is_unchanged(self):
        before = copy.deepcopy(self.users)
        display_name(self.users, ' USER-A ')
        self.assertEqual(self.users, before)
    def test_empty_id_is_unknown(self):
        self.assertIsNone(display_name(self.users, '   '))

if __name__ == '__main__': unittest.main()
