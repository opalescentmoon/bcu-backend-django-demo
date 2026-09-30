import json

import pytest
from django.urls import reverse

from catalog.models import Category
from core.testing import VendorProfileFactory, food_category, image_upload, make_food
from food.models import FoodProduct
from users.models import VendorStatus

pytestmark = pytest.mark.django_db


def create_payload(**overrides):
    return {
        "title": "Nasi Goreng Kampung",
        "description": "Pedas mantap",
        "category_id": food_category().id,
        "image": image_upload("nasgor.jpg", 2000, 1500),
        "variants": json.dumps(
            [{"name": "Reguler", "price": "20000", "stock": 10}, {"name": "Jumbo", "price": "25000", "stock": 0}]
        ),
        **overrides,
    }


class TestCatalog:
    def test_list_only_public_products(self, api_client, vendor, pending_vendor):
        visible = make_food(vendor, "Soto Ayam")
        make_food(vendor, "Tidak Tersedia", is_available=False)
        make_food(vendor, "Terhapus", is_deleted=True)
        make_food(pending_vendor, "Vendor Pending")
        res = api_client.get(reverse("food-list"))
        assert res.status_code == 200
        assert [r["id"] for r in res.data["results"]] == [visible.id]
        item = res.data["results"][0]
        assert item["min_price"] == "20000.00"
        assert item["in_stock"] is True
        assert item["vendor"]["slug"] == vendor.slug

    def test_filters_and_ordering(self, api_client, vendor):
        other = VendorProfileFactory(city="Jakarta")
        drink_cat = Category.objects.get(type="food", slug="minuman")
        a = make_food(vendor, "Es Teh Manis", variants=(("Reguler", "5000", 5),), category=drink_cat)
        b = make_food(vendor, "Nasi Goreng", variants=(("Reguler", "20000", 5),))
        c = make_food(other, "Nasi Uduk", variants=(("Reguler", "15000", 5),))

        def ids(**params):
            return [r["id"] for r in api_client.get(reverse("food-list"), params).data["results"]]

        assert ids(category="minuman") == [a.id]
        assert ids(city="jakarta") == [c.id]
        assert ids(vendor=vendor.slug, ordering="min_price") == [a.id, b.id]
        assert ids(min_price=10000, max_price=16000) == [c.id]
        assert set(ids(search="nasi")) == {b.id, c.id}
        assert ids(ordering="-min_price") == [b.id, c.id, a.id]

    def test_out_of_stock_flag(self, api_client, vendor):
        make_food(vendor, variants=(("Reguler", "10000", 0),))
        assert api_client.get(reverse("food-list")).data["results"][0]["in_stock"] is False

    def test_detail_hides_inactive_variants_for_public(self, api_client, vendor):
        food = make_food(vendor, variants=(("Reguler", "10000", 1), ("Jumbo", "15000", 1)))
        food.variants.filter(name="Jumbo").update(is_active=False)
        res = api_client.get(reverse("food-detail", args=[food.id]))
        assert [v["name"] for v in res.data["variants"]] == ["Reguler"]
        assert res.data["vendor"]["delivery_fee"] == "10000.00"

    def test_unavailable_detail_404_for_public_but_visible_to_owner(self, api_client, vendor_client, vendor):
        food = make_food(vendor, is_available=False)
        assert api_client.get(reverse("food-detail", args=[food.id])).status_code == 404
        assert vendor_client.get(reverse("food-detail", args=[food.id])).status_code == 200


class TestVendorManagement:
    def test_create_food_with_variants(self, vendor_client):
        res = vendor_client.post(reverse("food-list"), create_payload(), format="multipart")
        assert res.status_code == 201, res.data
        assert res.data["min_price"] == "20000.00"
        assert [v["name"] for v in res.data["variants"]] == ["Reguler", "Jumbo"]
        assert res.data["image_url"].endswith(".jpg")
        food = FoodProduct.objects.get(pk=res.data["id"])
        assert max(food.image.width, food.image.height) == 1200  # di-resize

    def test_create_requires_variant(self, vendor_client):
        res = vendor_client.post(reverse("food-list"), create_payload(variants="[]"), format="multipart")
        assert res.status_code == 400
        assert "variants" in res.data["error"]["details"]

    def test_rejects_invalid_image(self, vendor_client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        fake = SimpleUploadedFile("x.jpg", b"bukan gambar", content_type="image/jpeg")
        res = vendor_client.post(reverse("food-list"), create_payload(image=fake), format="multipart")
        assert res.status_code == 400

    def test_pending_vendor_cannot_create(self, client_for):
        vendor = VendorProfileFactory(status=VendorStatus.PENDING)
        res = client_for(vendor.user).post(reverse("food-list"), create_payload(), format="multipart")
        assert res.status_code == 403
        assert res.data["error"]["code"] == "vendor_not_approved"

    def test_image_only_vendor_cannot_create_food(self, client_for):
        vendor = VendorProfileFactory(sells_food=False, sells_images=True)
        res = client_for(vendor.user).post(reverse("food-list"), create_payload(), format="multipart")
        assert res.status_code == 403

    def test_customer_cannot_create(self, customer_client):
        assert customer_client.post(reverse("food-list"), create_payload(), format="multipart").status_code == 403

    def test_update_own_food_only(self, vendor_client, vendor, other_vendor):
        mine = make_food(vendor)
        theirs = make_food(other_vendor)
        assert vendor_client.patch(reverse("food-detail", args=[mine.id]), {"title": "Baru"}).data["title"] == "Baru"
        assert vendor_client.patch(reverse("food-detail", args=[theirs.id]), {"title": "X"}).status_code == 404

    def test_delete_without_orders_is_hard_delete(self, vendor_client, vendor):
        food = make_food(vendor)
        assert vendor_client.delete(reverse("food-detail", args=[food.id])).status_code == 204
        assert not FoodProduct.objects.filter(pk=food.pk).exists()

    def test_variant_crud_updates_min_price(self, vendor_client, vendor):
        food = make_food(vendor, variants=(("Reguler", "20000", 5),))
        res = vendor_client.post(
            reverse("food-add-variant", args=[food.id]), {"name": "Mini", "price": "12000", "stock": 3}
        )
        assert res.status_code == 201
        assert res.data["min_price"] == "12000.00"
        mini = food.variants.get(name="Mini")
        url = reverse("food-variant-detail", args=[food.id, mini.id])
        assert vendor_client.patch(url, {"price": "13000"}).data["price"] == "13000.00"
        assert vendor_client.delete(url).status_code == 204
        food.refresh_from_db()
        assert str(food.min_price) == "20000.00"

    def test_cannot_remove_last_active_variant(self, vendor_client, vendor):
        food = make_food(vendor)
        variant = food.variants.get()
        url = reverse("food-variant-detail", args=[food.id, variant.id])
        for res in (vendor_client.delete(url), vendor_client.patch(url, {"is_active": False})):
            assert res.status_code == 409
            assert res.data["error"]["code"] == "last_active_variant"

    def test_vendor_food_list_includes_unavailable(self, vendor_client, vendor, other_vendor):
        make_food(vendor, "A", is_available=False)
        make_food(other_vendor, "B")
        res = vendor_client.get(reverse("vendor-foods"))
        assert [r["title"] for r in res.data["results"]] == ["A"]


def test_categories_endpoint(api_client, db):
    res = api_client.get(reverse("category-list"), {"type": "image"})
    assert [c["slug"] for c in res.data] == ["fotografi-makanan", "ilustrasi", "banner-promosi"]
