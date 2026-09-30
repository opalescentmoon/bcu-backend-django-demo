import threading
from decimal import Decimal

import pytest
from django.core import mail
from django.db import connection
from django.urls import reverse

from core.testing import AddressFactory, UserFactory, make_food
from food.models import FoodVariant
from images.models import ImagePurchase
from orders import services
from orders.cart import get_cart
from orders.models import CartItem, Order, OrderStatus, Payment, VendorOrderStatus
from orders.payments import Notification

from .conftest import add_food, add_image, checkout, simulate

pytestmark = pytest.mark.django_db


def notify(order, status, amount=None, payment=None):
    payment = payment or order.payments.order_by("-created_at").first()
    return services.apply_payment_notification(
        Notification(
            provider_order_id=payment.provider_order_id,
            status=status,
            gross_amount=amount if amount is not None else payment.amount,
            payment_type="gopay",
        )
    )


class TestCheckout:
    def test_food_checkout_multi_vendor(self, customer_client, address, two_vendor_foods):
        jumbo, esteh = two_vendor_foods
        add_food(customer_client, jumbo, 2)
        add_food(customer_client, esteh, 1)
        res = checkout(customer_client, "food", address)
        assert res.status_code == 201, res.data
        order_data = res.data["order"]
        assert order_data["order_number"].startswith("FD-")
        assert (order_data["subtotal"], order_data["delivery_fee_total"], order_data["total_amount"]) == (
            "55000.00",
            "18000.00",
            "73000.00",
        )
        assert res.data["payment"]["provider"] == "simulation"

        order = Order.objects.get(order_number=order_data["order_number"])
        rows = {vo.vendor_id: vo for vo in order.vendor_orders.all()}
        shop_a = rows[jumbo.food.vendor_id]
        assert (shop_a.subtotal, shop_a.commission_amount, shop_a.vendor_earning) == (
            Decimal("50000"),
            Decimal("5000"),
            Decimal("55000"),
        )
        shop_b = rows[esteh.food.vendor_id]
        assert (shop_b.commission_amount, shop_b.vendor_earning) == (Decimal("500"), Decimal("12500"))
        jumbo.refresh_from_db()
        assert jumbo.stock == 8  # stok direservasi
        assert order.shipping_recipient == address.recipient_name
        assert not CartItem.objects.filter(item_type="food").exists()

    def test_checkout_only_processes_selected_type(self, customer_client, address, two_vendor_foods, image_product):
        add_food(customer_client, two_vendor_foods[0])
        add_image(customer_client, image_product.licenses.first())
        checkout(customer_client, "food", address)
        assert list(get_cart(customer_client.user).items.values_list("item_type", flat=True)) == ["image"]

    def test_price_snapshot(self, customer_client, address, two_vendor_foods):
        jumbo, _ = two_vendor_foods
        add_food(customer_client, jumbo)
        number = checkout(customer_client, "food", address).data["order"]["order_number"]
        FoodVariant.objects.filter(pk=jumbo.pk).update(price=99999)
        item = Order.objects.get(order_number=number).items.get()
        assert item.unit_price == Decimal("25000")

    def test_insufficient_stock_at_checkout(self, customer_client, address, vendor):
        variant = make_food(vendor, variants=(("Reguler", "10000", 2),)).variants.get()
        add_food(customer_client, variant, 2)
        FoodVariant.objects.filter(pk=variant.pk).update(stock=1)
        res = checkout(customer_client, "food", address)
        assert res.status_code == 409
        assert res.data["error"]["code"] == "cart_item_invalid"
        assert res.data["error"]["details"]["items"][0]["reason"] == "insufficient_stock"
        assert not Order.objects.exists()

    def test_requires_verified_email(self, client_for, two_vendor_foods):
        user = UserFactory(is_email_verified=False)
        address = AddressFactory(user=user)
        client = client_for(user)
        add_food(client, two_vendor_foods[0])
        res = checkout(client, "food", address)
        assert res.status_code == 403
        assert res.data["error"]["code"] == "email_not_verified"

    def test_food_requires_own_address(self, customer_client, two_vendor_foods):
        add_food(customer_client, two_vendor_foods[0])
        assert checkout(customer_client, "food").status_code == 400
        assert checkout(customer_client, "food", AddressFactory()).status_code == 400

    def test_empty_cart(self, customer_client):
        assert checkout(customer_client, "image").status_code == 400

    @pytest.mark.django_db(transaction=True, serialized_rollback=True)
    def test_concurrent_checkout_does_not_oversell(self, vendor):
        variant = make_food(vendor, variants=(("Terakhir", "10000", 1),)).variants.get()
        buyers = []
        for _ in range(2):
            user = UserFactory()
            address = AddressFactory(user=user)
            CartItem.objects.create(cart=get_cart(user), item_type="food", food_variant=variant, quantity=1)
            buyers.append((user, address))

        results = []
        barrier = threading.Barrier(2)

        def run(user, address):
            try:
                barrier.wait()
                services.checkout(user, "food", address=address)
                results.append("ok")
            except Exception as exc:  # noqa: BLE001
                results.append(getattr(exc, "code", type(exc).__name__))
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=b) for b in buyers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == ["insufficient_stock", "ok"]
        variant.refresh_from_db()
        assert variant.stock == 0


class TestPayment:
    def _food_order(self, client, address, variant, qty=1):
        add_food(client, variant, qty)
        return Order.objects.get(order_number=checkout(client, "food", address).data["order"]["order_number"])

    def test_settlement_food(self, customer_client, address, two_vendor_foods, django_capture_on_commit_callbacks):
        order = self._food_order(customer_client, address, two_vendor_foods[0])
        mail.outbox.clear()
        with django_capture_on_commit_callbacks(execute=True):
            res = simulate(customer_client, order.order_number)
        assert res.status_code == 200
        assert res.data["status"] == "paid"
        assert {vo["status"] for vo in res.data["vendor_orders"]} == {"new"}
        assert res.data["payment_method"] == "simulation"
        assert len(mail.outbox) == 2  # customer + vendor

    def test_settlement_image_grants_download(self, customer_client, image_product):
        add_image(customer_client, image_product.licenses.get(license_type="commercial"))
        number = checkout(customer_client, "image").data["order"]["order_number"]
        res = simulate(customer_client, number)
        assert res.data["status"] == "completed"
        assert ImagePurchase.objects.filter(
            user=customer_client.user, image=image_product, license_type="commercial"
        ).exists()
        assert customer_client.get(reverse("image-download", args=[image_product.id])).status_code == 200

    def test_duplicate_notification_idempotent(
        self, customer_client, image_product, django_capture_on_commit_callbacks
    ):
        add_image(customer_client, image_product.licenses.first())
        order = Order.objects.get(order_number=checkout(customer_client, "image").data["order"]["order_number"])
        mail.outbox.clear()
        with django_capture_on_commit_callbacks(execute=True):
            notify(order, "settlement")
            notify(order, "settlement")
        assert ImagePurchase.objects.count() == 1
        assert len(mail.outbox) == 2

    def test_amount_mismatch_ignored(self, customer_client, address, two_vendor_foods):
        order = self._food_order(customer_client, address, two_vendor_foods[0])
        notify(order, "settlement", amount=Decimal("1"))
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    def test_expire_restores_stock(self, customer_client, address, two_vendor_foods):
        jumbo = two_vendor_foods[0]
        order = self._food_order(customer_client, address, jumbo, 3)
        simulate(customer_client, order.order_number, "expire")
        order.refresh_from_db()
        jumbo.refresh_from_db()
        assert order.status == OrderStatus.EXPIRED
        assert jumbo.stock == 10
        assert set(order.vendor_orders.values_list("status", flat=True)) == {VendorOrderStatus.CANCELLED}

    def test_late_settlement_after_expired_flags_refund(self, customer_client, address, two_vendor_foods):
        order = self._food_order(customer_client, address, two_vendor_foods[0])
        payment = order.payments.get()
        services._close_unpaid_order(order, OrderStatus.EXPIRED)
        notify(order, "settlement", payment=payment)
        order.refresh_from_db()
        assert order.status == OrderStatus.EXPIRED
        assert order.refund_status == "pending"

    def test_deny_then_pay_again(self, customer_client, address, two_vendor_foods):
        order = self._food_order(customer_client, address, two_vendor_foods[0])
        simulate(customer_client, order.order_number, "failure")
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING
        res = customer_client.post(reverse("order-pay", args=[order.order_number]))
        assert res.status_code == 200
        assert res.data["provider_order_id"].endswith("-2")
        assert order.payments.count() == 2

    def test_simulate_disabled_when_midtrans(self, customer_client, address, two_vendor_foods, settings):
        order = self._food_order(customer_client, address, two_vendor_foods[0])
        settings.PAYMENT_PROVIDER = "midtrans"
        assert simulate(customer_client, order.order_number).status_code == 404

    def test_other_user_cannot_see_order(self, customer_client, address, two_vendor_foods, client_for):
        order = self._food_order(customer_client, address, two_vendor_foods[0])
        assert client_for(UserFactory()).get(reverse("order-detail", args=[order.order_number])).status_code == 404

    def test_lazy_expiry_on_detail(self, customer_client, address, two_vendor_foods, time_machine, client_for):
        order = self._food_order(customer_client, address, two_vendor_foods[0])
        time_machine.move_to(order.expires_at, tick=False)
        res = client_for(customer_client.user).get(reverse("order-detail", args=[order.order_number]))
        assert res.data["status"] == "expired"
        assert res.data["can_pay"] is False

    def test_expire_command(self, customer_client, address, two_vendor_foods, time_machine):
        from django.core.management import call_command

        order = self._food_order(customer_client, address, two_vendor_foods[0])
        fresh = self._food_order(customer_client, address, two_vendor_foods[1])
        Order.objects.filter(pk=fresh.pk).update(expires_at=order.expires_at.replace(year=order.expires_at.year + 1))
        time_machine.move_to(order.expires_at, tick=False)
        call_command("expire_pending_orders")
        assert Order.objects.get(pk=order.pk).status == "expired"
        assert Order.objects.get(pk=fresh.pk).status == "pending"


def test_history_filters(customer_client, address, two_vendor_foods, image_product):
    add_food(customer_client, two_vendor_foods[0])
    checkout(customer_client, "food", address)
    add_image(customer_client, image_product.licenses.first())
    checkout(customer_client, "image")
    res = customer_client.get(reverse("order-history"), {"order_type": "image"})
    assert res.data["count"] == 1
    assert res.data["results"][0]["order_type"] == "image"
    assert customer_client.get(reverse("order-history")).data["count"] == 2


def test_payment_rows(customer_client, address, two_vendor_foods):
    add_food(customer_client, two_vendor_foods[0])
    checkout(customer_client, "food", address)
    payment = Payment.objects.get()
    assert payment.provider_order_id.endswith("-1")
    assert payment.amount == Decimal("35000")
