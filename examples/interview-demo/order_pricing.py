"""Order pricing exercise. See README for the business rules and repair task."""

from decimal import Decimal, ROUND_HALF_UP


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def items_subtotal(items):
    total = Decimal("0")
    for item in items:
        price = money(item["price"])
        quantity = item["quantity"]
        if price < 0 or type(quantity) is not int or quantity <= 0:
            raise ValueError("price must be non-negative; quantity must be a positive integer")
        total += price * quantity
    return money(total)


def discounted_total(subtotal, discount_percent):
    percent = Decimal(str(discount_percent))
    if not 0 <= percent <= 100:
        raise ValueError("discount_percent must be between 0 and 100")
    return money(subtotal * (100 - percent) / 100)


def shipping_fee(discounted_amount):
    return money(0 if discounted_amount >= 100 else 8)


def calculate_total(items, discount_percent=0):
    subtotal = items_subtotal(items)
    discounted = discounted_total(subtotal, discount_percent)
    if not items:
        return money(0)
    return money(discounted + shipping_fee(discounted))
