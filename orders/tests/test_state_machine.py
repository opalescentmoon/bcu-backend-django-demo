import pytest
from django.urls import reverse

from core.testing import make_food
from orders.models import Order, VendorOrder

from .conftest import add_food, add_image, checkout, simulate

pytestmark = pytest.mark.django_db


@pytest.fixture
def paid_food_order(customer_client, address, two_vendor_foods):
    jumbo, esteh = two_vendor_foods
    add_food(customer_client, jumbo, 2)
    add_food(customer_client, esteh, 1)
    number = checkout(customer_client, "food", address).data["order"]["order_number"]
    simulate(customer_client, number)
    return Order.objects.get(order_number=number)


def vendor_order_of(order, vendor):
    return order.vendor_orders.get(vendor=vendor)


def set_status(client, vendor_order, status, reason=""):
    return client.post(reverse("vendor-order-status", args=[vendor_order.id]), {"status": status, "reason": reason})


class TestVendorTransitions:
    def test_full_happy_path(self, paid_food_order, vendor, vendor_client, client_for, customer_client):
        vo_a = vendor_order_of(paid_food_order, vendor)
        vo_b = paid_food_order.vendor_orders.exclude(pk=vo_a.pk).get()
        client_b = client_for(vo_b.vendor.user)

        assert set_status(vendor_client, vo_a, "processing").data["status"] == "processing"
        assert set_status(vendor_client, vo_a, "shipped").data["status"] == "shipped"
        res = customer_client.post(reverse("order-confirm-received", args=[paid_food_order.order_number, vo_a.id]))
        assert res.status_code == 200
        assert res.data["status"] == "paid"  # toko B belum selesai

        for status in ("processing", "shipped", "completed"):
            assert set_status(client_b, vo_b, status).status_code == 200
        paid_food_order.refresh_from_db()
        assert paid_food_order.status == "completed"
        detail = customer_client.get(reverse("order-detail", args=[paid_food_order.order_number])).data
        timeline = [p["status"] for p in detail["vendor_orders"][0]["timeline"]]
        assert timeline == ["new", "processing", "shipped", "completed"]

    @pytest.mark.parametrize("path", [["shipped"], ["processing", "completed"], ["processing", "cancelled"]])
    def test_invalid_transitions(self, paid_food_order, vendor, vendor_client, path):
        vo = vendor_order_of(paid_food_order, vendor)
        *ok, bad = path
        for status in ok:
            assert set_status(vendor_client, vo, status).status_code == 200
        res = set_status(vendor_client, vo, bad, reason="x")
        assert res.status_code == 409
        assert res.data["error"]["code"] == "invalid_transition"

    def test_other_vendor_gets_404(self, paid_food_order, vendor, client_for, other_vendor):
        vo = vendor_order_of(paid_food_order, vendor)
        assert set_status(client_for(other_vendor.user), vo, "processing").status_code == 404

    def test_customer_cannot_confirm_before_shipped(self, paid_food_order, vendor, customer_client):
        vo = vendor_order_of(paid_food_order, vendor)
        res = customer_client.post(reverse("order-confirm-received", args=[paid_food_order.order_number, vo.id]))
        assert res.status_code == 409

    def test_reject_requires_reason_and_restores_stock(self, paid_food_order, vendor, vendor_client):
        vo = vendor_order_of(paid_food_order, vendor)
        assert set_status(vendor_client, vo, "cancelled").status_code == 400
        res = set_status(vendor_client, vo, "cancelled", reason="Bahan habis")
        assert res.status_code == 200
        variant = vo.items.get().food_variant
        variant.refresh_from_db()
        assert variant.stock == 10
        paid_food_order.refresh_from_db()
        assert paid_food_order.refund_status == "pending"
        assert paid_food_order.status == "paid"

    def test_one_rejected_one_completed(self, paid_food_order, vendor, vendor_client, client_for):
        vo_a = vendor_order_of(paid_food_order, vendor)
        vo_b = paid_food_order.vendor_orders.exclude(pk=vo_a.pk).get()
        set_status(vendor_client, vo_a, "cancelled", reason="Tutup")
        client_b = client_for(vo_b.vendor.user)
        for status in ("processing", "shipped", "completed"):
            set_status(client_b, vo_b, status)
        paid_food_order.refresh_from_db()
        assert (paid_food_order.status, paid_food_order.refund_status) == ("completed", "pending")

    def test_all_rejected_cancels_order(self, paid_food_order, client_for):
        for vo in paid_food_order.vendor_orders.select_related("vendor__user"):
            set_status(client_for(vo.vendor.user), vo, "cancelled", reason="Tutup")
        paid_food_order.refresh_from_db()
        assert paid_food_order.status == "cancelled"


class TestCustomerCancel:
    def test_cancel_pending(self, customer_client, address, two_vendor_foods):
        jumbo, _ = two_vendor_foods
        add_food(customer_client, jumbo, 3)
        number = checkout(customer_client, "food", address).data["order"]["order_number"]
        res = customer_client.post(reverse("order-cancel", args=[number]), {"reason": "Salah alamat"})
        assert res.data["status"] == "cancelled"
        assert res.data["refund_status"] == "none"
        jumbo.refresh_from_db()
        assert jumbo.stock == 10

    def test_cancel_paid_while_all_new(self, paid_food_order, customer_client):
        res = customer_client.post(reverse("order-cancel", args=[paid_food_order.order_number]))
        assert res.data["status"] == "cancelled"
        assert res.data["refund_status"] == "pending"

    def test_cannot_cancel_once_processing(self, paid_food_order, vendor, vendor_client, customer_client):
        set_status(vendor_client, vendor_order_of(paid_food_order, vendor), "processing")
        res = customer_client.post(reverse("order-cancel", args=[paid_food_order.order_number]))
        assert res.status_code == 409
        assert res.data["error"]["code"] == "order_not_cancellable"

    def test_digital_goods_non_refundable(self, customer_client, image_product):
        add_image(customer_client, image_product.licenses.first())
        number = checkout(customer_client, "image").data["order"]["order_number"]
        simulate(customer_client, number)
        res = customer_client.post(reverse("order-cancel", args=[number]))
        assert res.status_code == 409
        assert res.data["error"]["code"] == "digital_goods_non_refundable"


class TestVendorViews:
    def test_vendor_order_list_hides_unpaid_and_scopes_customer_data(
        self, paid_food_order, vendor, vendor_client, customer_client, address, image_product, client_for
    ):
        add_food(customer_client, make_food(vendor, "Belum Bayar").variants.get())
        checkout(customer_client, "food", address)  # pending, tidak tampil
        res = vendor_client.get(reverse("vendor-orders"))
        assert res.data["count"] == 1
        row = res.data["results"][0]
        assert row["customer"]["address"] == paid_food_order.shipping_address
        assert row["allowed_transitions"] == ["cancelled", "processing"]

        add_image(customer_client, image_product.licenses.first())
        simulate(customer_client, checkout(customer_client, "image").data["order"]["order_number"])
        image_rows = client_for(image_product.vendor.user).get(reverse("vendor-orders")).data["results"]
        assert set(image_rows[0]["customer"]) == {"username"}

    def test_dashboard(self, paid_food_order, vendor, vendor_client):
        vo = vendor_order_of(paid_food_order, vendor)
        for status in ("processing", "shipped", "completed"):
            set_status(vendor_client, vo, status)
        data = vendor_client.get(reverse("vendor-dashboard"), {"period": "7d"}).data
        assert data["summary"]["gross_sales"] == 50000
        assert data["summary"]["net_earning"] == 55000
        assert data["summary"]["orders_count"] == 1
        assert data["summary"]["unpaid_payout"] == 55000
        assert data["top_products"][0]["title"] == "Nasi Goreng"
        assert data["top_products"][0]["quantity"] == 2
        assert len(data["series"]) == 1

    def test_food_with_orders_is_soft_deleted(self, paid_food_order, vendor, vendor_client):
        food = VendorOrder.objects.get(order=paid_food_order, vendor=vendor).items.get().food
        assert vendor_client.delete(reverse("food-detail", args=[food.id])).status_code == 204
        food.refresh_from_db()
        assert food.is_deleted
