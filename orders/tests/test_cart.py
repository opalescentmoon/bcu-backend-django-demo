import pytest
from django.urls import reverse

from core.testing import make_food, make_image_purchase

from .conftest import add_food, add_image

pytestmark = pytest.mark.django_db


def test_cart_groups_food_by_vendor_and_totals(customer_client, two_vendor_foods, image_product):
    jumbo, esteh = two_vendor_foods
    add_food(customer_client, jumbo, 2)
    add_food(customer_client, esteh, 1)
    res = add_image(customer_client, image_product.licenses.get(license_type="commercial"))
    assert res.status_code == 201
    data = res.data
    assert [g["subtotal"] for g in data["food"]["groups"]] == ["50000.00", "5000.00"]
    assert data["food"]["subtotal"] == "55000.00"
    assert data["food"]["delivery_fee_total"] == "18000.00"
    assert data["food"]["total"] == "73000.00"
    assert data["image"]["total"] == "150000.00"
    assert data["item_count"] == 3


def test_adding_same_variant_sums_quantity(customer_client, two_vendor_foods):
    jumbo, _ = two_vendor_foods
    add_food(customer_client, jumbo, 2)
    res = add_food(customer_client, jumbo, 3)
    assert res.data["food"]["groups"][0]["items"][0]["quantity"] == 5


def test_cannot_add_more_than_stock(customer_client, vendor):
    variant = make_food(vendor, variants=(("Reguler", "10000", 1),)).variants.get()
    res = add_food(customer_client, variant, 2)
    assert res.status_code == 409
    assert res.data["error"]["code"] == "insufficient_stock"


def test_vendor_cannot_buy_own_product(vendor_client, vendor):
    variant = make_food(vendor).variants.get()
    res = add_food(vendor_client, variant)
    assert res.status_code == 409
    assert res.data["error"]["code"] == "own_product"


def test_store_closed(customer_client, vendor):
    variant = make_food(vendor).variants.get()
    vendor.is_open = False
    vendor.save()
    assert add_food(customer_client, variant).data["error"]["code"] == "store_closed"


def test_item_becomes_invalid_after_vendor_suspended(customer_client, vendor):
    variant = make_food(vendor).variants.get()
    add_food(customer_client, variant)
    vendor.status = "suspended"
    vendor.save()
    data = customer_client.get(reverse("cart")).data
    item = data["food"]["groups"][0]["items"][0]
    assert item["is_valid"] is False
    assert item["invalid_reason"] == "vendor_inactive"
    assert data["food"]["total"] == "0"


def test_switching_license_replaces_item(customer_client, image_product):
    add_image(customer_client, image_product.licenses.get(license_type="personal"))
    res = add_image(customer_client, image_product.licenses.get(license_type="commercial"))
    assert [i["license"]["license_type"] for i in res.data["image"]["items"]] == ["commercial"]


def test_already_purchased_license(customer_client, image_product):
    make_image_purchase(customer_client.user, image_product, "commercial")
    res = add_image(customer_client, image_product.licenses.get(license_type="personal"))
    assert res.status_code == 409
    assert res.data["error"]["code"] == "already_purchased"


def test_upgrade_personal_to_commercial_allowed(customer_client, image_product):
    make_image_purchase(customer_client.user, image_product, "personal")
    assert add_image(customer_client, image_product.licenses.get(license_type="commercial")).status_code == 201


def test_update_and_remove_items(customer_client, two_vendor_foods, client_for):
    jumbo, _ = two_vendor_foods
    item_id = add_food(customer_client, jumbo).data["food"]["groups"][0]["items"][0]["id"]
    res = customer_client.patch(reverse("cart-item-detail", args=[item_id]), {"quantity": 4})
    assert res.data["food"]["groups"][0]["items"][0]["quantity"] == 4
    assert customer_client.patch(reverse("cart-item-detail", args=[item_id]), {"quantity": 99}).status_code == 409
    from core.testing import UserFactory

    assert client_for(UserFactory()).delete(reverse("cart-item-detail", args=[item_id])).status_code == 404
    assert customer_client.delete(reverse("cart-item-detail", args=[item_id])).status_code == 204
    assert customer_client.get(reverse("cart")).data["item_count"] == 0
