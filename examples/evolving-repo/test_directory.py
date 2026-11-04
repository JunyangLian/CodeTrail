import unittest

from directory import normalize_user_id
from users import lookup_user


class DirectoryTests(unittest.TestCase):
    def test_whitespace(self):
        self.assertEqual(normalize_user_id('  Ada  '), 'ada')

    def test_unicode_case(self):
        self.assertEqual(normalize_user_id('Straße'), 'strasse')

    def test_numeric_identifier(self):
        self.assertEqual(normalize_user_id(42), '42')

    def test_empty_identifier(self):
        self.assertEqual(normalize_user_id('  '), '')

    def test_plain_lookup(self):
        self.assertEqual(lookup_user({'ada': 'engineer'}, 'ada'), 'engineer')

    def test_lookup_normalizes(self):
        self.assertEqual(lookup_user({'ada': 'engineer'}, ' ADA '), 'engineer')

    def test_missing(self):
        self.assertIsNone(lookup_user({}, 'missing'))

    def test_input_unchanged(self):
        rows = {'ada': 'engineer'}
        lookup_user(rows, ' ADA ')
        self.assertEqual(rows, {'ada': 'engineer'})
