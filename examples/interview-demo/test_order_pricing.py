"""Business acceptance tests. Keep these unchanged while repairing the exercise."""

import unittest
from decimal import Decimal

from order_pricing import calculate_total, discounted_total, items_subtotal, shipping_fee


class OrderPricingTests(unittest.TestCase):
    def test_subtotal_counts_quantities(self):
        self.assertEqual(items_subtotal([{"price": "19.90", "quantity": 2},
                                        {"price": "5.25", "quantity": 3}]), Decimal("55.55"))

    def test_empty_subtotal(self):
        self.assertEqual(items_subtotal([]), Decimal("0.00"))

    def test_negative_price_is_rejected(self):
        with self.assertRaises(ValueError):
            items_subtotal([{"price": "-1", "quantity": 1}])

    def test_invalid_quantities_are_rejected(self):
        for quantity in (0, -1, 1.5, True):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                items_subtotal([{"price": "10", "quantity": quantity}])

    def test_ten_percent_discount(self):
        self.assertEqual(discounted_total(Decimal("100"), 10), Decimal("90.00"))

    def test_zero_and_full_discount(self):
        self.assertEqual(discounted_total(Decimal("100"), 0), Decimal("100.00"))
        self.assertEqual(discounted_total(Decimal("100"), 100), Decimal("0.00"))

    def test_discount_rounds_half_up(self):
        self.assertEqual(discounted_total(Decimal("19.99"), 50), Decimal("10.00"))

    def test_invalid_discount_is_rejected(self):
        for percent in (-1, 101):
            with self.subTest(percent=percent), self.assertRaises(ValueError):
                discounted_total(Decimal("100"), percent)

    def test_shipping_threshold_is_inclusive(self):
        self.assertEqual(shipping_fee(Decimal("100")), Decimal("0.00"))
        self.assertEqual(shipping_fee(Decimal("99.99")), Decimal("8.00"))

    def test_shipping_uses_amount_after_discount(self):
        self.assertEqual(calculate_total([{"price": "100", "quantity": 1}], 10), Decimal("98.00"))
        self.assertEqual(calculate_total([{"price": "125", "quantity": 1}], 20), Decimal("100.00"))

    def test_order_combines_quantity_discount_and_shipping(self):
        self.assertEqual(calculate_total([{"price": "30", "quantity": 2}], 10), Decimal("62.00"))

    def test_empty_order_has_no_shipping(self):
        self.assertEqual(calculate_total([]), Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
