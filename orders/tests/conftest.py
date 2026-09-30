import pytest
from django.urls import reverse

from core.testing import VendorProfileFactory, make_food, make_image_product


@pytest.fixture
def two_vendor_foods(vendor):
    """Toko A (ongkir 10rb) Nasi Goreng Jumbo 25rb; Toko B (ongkir 8rb) Es Teh 5rb — contoh docs/05 §3."""
    shop_b = VendorProfileFactory(delivery_fee="8000")
    nasgor = make_food(vendor, "Nasi Goreng", variants=(("Reguler", "20000", 10), ("Jumbo", "25000", 10)))
    esteh = make_food(shop_b, "Es Teh", variants=(("Reguler", "5000", 10),))
    return nasgor.variants.get(name="Jumbo"), esteh.variants.get()


@pytest.fixture
def image_product(other_vendor):
    return make_image_product(other_vendor)


def add_food(client, variant, quantity=1):
    return client.post(
        reverse("cart-items"), {"item_type": "food", "food_variant_id": variant.id, "quantity": quantity}
    )


def add_image(client, license_option):
    return client.post(reverse("cart-items"), {"item_type": "image", "image_license_id": license_option.id})


def checkout(client, order_type, address=None):
    payload = {"order_type": order_type}
    if address:
        payload["address_id"] = address.id
    return client.post(reverse("checkout"), payload)


def simulate(client, order_number, result="success"):
    return client.post(reverse("payment-simulate", args=[order_number]), {"result": result})
