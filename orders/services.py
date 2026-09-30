"""Logika transaksi: checkout, pembayaran, dan state machine pesanan (docs/05)."""

import logging
import secrets
import string
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import exceptions

from core.emails import send_templated_email
from core.exceptions import BusinessError
from core.models import PlatformSetting
from core.utils import round_rupiah
from food.models import FoodVariant
from images.models import ImagePurchase

from . import cart as cart_logic
from .models import (
    ItemType,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentStatus,
    RefundStatus,
    VendorOrder,
    VendorOrderStatus,
)
from .payments import Notification, get_provider

logger = logging.getLogger(__name__)

FINAL_VENDOR_STATUSES = {VendorOrderStatus.COMPLETED, VendorOrderStatus.CANCELLED}
VENDOR_TRANSITIONS = {
    VendorOrderStatus.NEW: {VendorOrderStatus.PROCESSING, VendorOrderStatus.CANCELLED},
    VendorOrderStatus.PROCESSING: {VendorOrderStatus.SHIPPED},
    VendorOrderStatus.SHIPPED: {VendorOrderStatus.COMPLETED},
}
TIMESTAMP_FIELD = {
    VendorOrderStatus.PROCESSING: "processing_at",
    VendorOrderStatus.SHIPPED: "shipped_at",
    VendorOrderStatus.COMPLETED: "completed_at",
    VendorOrderStatus.CANCELLED: "cancelled_at",
}


# --- Nomor pesanan ---------------------------------------------------------------
def generate_order_number(order_type: str) -> str:
    prefix = "FD" if order_type == ItemType.FOOD else "IM"
    alphabet = string.ascii_uppercase + string.digits
    date = timezone.localdate().strftime("%Y%m%d")
    while True:
        number = f"{prefix}-{date}-{''.join(secrets.choice(alphabet) for _ in range(6))}"
        if not Order.objects.filter(order_number=number).exists():
            return number


# --- Checkout --------------------------------------------------------------------
@transaction.atomic
def checkout(user, order_type: str, address=None, notes: str = "") -> Order:
    cart = cart_logic.get_cart(user)
    items = list(cart_logic.cart_items(cart).filter(item_type=order_type))
    if not items:
        raise exceptions.ValidationError({"order_type": ["Tidak ada item bertipe ini di keranjang."]})
    if order_type == ItemType.FOOD and address is None:
        raise exceptions.ValidationError({"address_id": ["Alamat pengiriman wajib diisi."]})

    invalid = [{"cart_item_id": i.pk, "reason": r} for i in items if (r := cart_logic.item_invalid_reason(i, user))]
    if invalid:
        raise BusinessError(
            "cart_item_invalid", "Beberapa item di keranjang tidak dapat diproses.", details={"items": invalid}
        )

    if order_type == ItemType.FOOD:
        _reserve_stock(items)

    settings_row = PlatformSetting.load()
    grouped = defaultdict(list)
    for item in items:
        vendor = item.food_variant.food.vendor if order_type == ItemType.FOOD else item.image_license.image.vendor
        grouped[vendor].append(item)

    subtotal = Decimal("0")
    delivery_total = Decimal("0")
    vendor_rows = []
    for vendor, vendor_items in grouped.items():
        vendor_subtotal = sum((_unit_price(i) * i.quantity for i in vendor_items), Decimal("0"))
        delivery_fee = vendor.delivery_fee if order_type == ItemType.FOOD else Decimal("0")
        subtotal += vendor_subtotal
        delivery_total += delivery_fee
        vendor_rows.append((vendor, vendor_items, vendor_subtotal, delivery_fee))

    order = Order.objects.create(
        order_number=generate_order_number(order_type),
        user=user,
        order_type=order_type,
        subtotal=subtotal,
        delivery_fee_total=delivery_total,
        total_amount=subtotal + delivery_total,
        notes=notes,
        expires_at=timezone.now() + timedelta(minutes=settings_row.order_payment_timeout_minutes),
        **_shipping_snapshot(address),
    )
    for vendor, vendor_items, vendor_subtotal, delivery_fee in vendor_rows:
        rate = vendor.commission_rate if vendor.commission_rate is not None else settings_row.default_commission_rate
        commission = round_rupiah(vendor_subtotal * rate)
        vendor_order = VendorOrder.objects.create(
            order=order,
            vendor=vendor,
            subtotal=vendor_subtotal,
            delivery_fee=delivery_fee,
            commission_rate=rate,
            commission_amount=commission,
            vendor_earning=vendor_subtotal - commission + delivery_fee,
        )
        OrderItem.objects.bulk_create([_order_item(order, vendor_order, i) for i in vendor_items])

    cart.items.filter(pk__in=[i.pk for i in items]).delete()
    logger.info("Checkout", extra={"order_number": order.order_number, "user_id": user.pk})
    return order


def _unit_price(item) -> Decimal:
    return item.food_variant.price if item.item_type == ItemType.FOOD else item.image_license.price


def _shipping_snapshot(address) -> dict:
    if address is None:
        return {}
    return {
        "shipping_recipient": address.recipient_name,
        "shipping_phone": address.phone,
        "shipping_address": address.full_address + (f" ({address.notes})" if address.notes else ""),
        "shipping_latitude": address.latitude,
        "shipping_longitude": address.longitude,
    }


def _order_item(order, vendor_order, item) -> OrderItem:
    if item.item_type == ItemType.FOOD:
        variant = item.food_variant
        return OrderItem(
            order=order,
            vendor_order=vendor_order,
            product_type=ItemType.FOOD,
            food=variant.food,
            food_variant=variant,
            title_snapshot=variant.food.title,
            variant_snapshot=variant.name,
            unit_price=variant.price,
            quantity=item.quantity,
            line_total=variant.price * item.quantity,
        )
    license_option = item.image_license
    return OrderItem(
        order=order,
        vendor_order=vendor_order,
        product_type=ItemType.IMAGE,
        image=license_option.image,
        license_type=license_option.license_type,
        title_snapshot=license_option.image.title,
        unit_price=license_option.price,
        quantity=1,
        line_total=license_option.price,
    )


def _reserve_stock(items) -> None:
    """Kunci baris varian (urut id untuk mencegah deadlock) lalu potong stok (docs/03 §6)."""
    wanted = defaultdict(int)
    for item in items:
        wanted[item.food_variant_id] += item.quantity
    locked = FoodVariant.objects.select_for_update().filter(pk__in=wanted).order_by("pk")
    short = [{"variant_id": v.pk, "available": v.stock} for v in locked if v.stock < wanted[v.pk]]
    if short:
        raise BusinessError("insufficient_stock", "Stok tidak mencukupi.", details={"variants": short})
    for variant_id, quantity in wanted.items():
        FoodVariant.objects.filter(pk=variant_id).update(stock=F("stock") - quantity)


def _restore_stock(items) -> None:
    for item in items:
        if item.product_type == ItemType.FOOD:
            FoodVariant.objects.filter(pk=item.food_variant_id).update(stock=F("stock") + item.quantity)


# --- Pembayaran ------------------------------------------------------------------
def start_payment(order: Order) -> Payment:
    """Buat percobaan pembayaran baru (dipanggil setelah checkout atau /pay/)."""
    provider = get_provider()
    attempt = order.payments.count() + 1
    payment = Payment.objects.create(
        order=order,
        provider=provider.name,
        provider_order_id=f"{order.order_number}-{attempt}",
        amount=order.total_amount,
    )
    init = provider.create_payment(payment)
    payment.snap_token, payment.redirect_url = init.snap_token, init.redirect_url
    payment.save(update_fields=["snap_token", "redirect_url", "updated_at"])
    return payment


def apply_payment_notification(notification: Notification) -> Payment | None:
    """Satu-satunya jalur perubahan status karena pembayaran. Idempoten."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().filter(provider_order_id=notification.provider_order_id).first()
        if payment is None:
            logger.warning(
                "Notifikasi untuk payment tidak dikenal", extra={"provider_order_id": notification.provider_order_id}
            )
            return None
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        payment.raw_notification = notification.raw

        if Decimal(notification.gross_amount) != payment.amount:
            logger.error(
                "gross_amount tidak cocok",
                extra={"provider_order_id": payment.provider_order_id, "gross_amount": str(notification.gross_amount)},
            )
            payment.save(update_fields=["raw_notification", "updated_at"])
            return payment

        status = notification.status
        if status is None or status == payment.status or payment.status == PaymentStatus.SETTLEMENT:
            payment.save(update_fields=["raw_notification", "updated_at"])
            return payment

        payment.status = status
        payment.payment_type = notification.payment_type or payment.payment_type
        payment.provider_transaction_id = notification.transaction_id or payment.provider_transaction_id
        if status == PaymentStatus.SETTLEMENT:
            payment.settled_at = timezone.now()
        payment.save()

        other_pending = order.payments.exclude(pk=payment.pk).filter(status=PaymentStatus.PENDING).exists()
        if status == PaymentStatus.SETTLEMENT:
            if order.status == OrderStatus.PENDING:
                _mark_order_paid(order, payment)
            elif order.status in (OrderStatus.EXPIRED, OrderStatus.CANCELLED):
                order.refund_status = RefundStatus.PENDING  # pembayaran terlambat (docs/05 §4.2)
                order.save(update_fields=["refund_status", "updated_at"])
        elif status == PaymentStatus.EXPIRE and order.status == OrderStatus.PENDING and not other_pending:
            _close_unpaid_order(order, OrderStatus.EXPIRED)
        elif status == PaymentStatus.CANCEL and order.status == OrderStatus.PENDING and not other_pending:
            _close_unpaid_order(order, OrderStatus.CANCELLED, reason="Pembayaran dibatalkan.")
    return payment


def _mark_order_paid(order: Order, payment: Payment) -> None:
    now = timezone.now()
    order.status = OrderStatus.PAID
    order.paid_at = now
    order.payment_method = payment.payment_type
    vendor_orders = list(order.vendor_orders.select_related("vendor__user"))

    if order.order_type == ItemType.IMAGE:
        for item in order.items.all():
            try:
                with transaction.atomic():
                    ImagePurchase.objects.create(
                        user=order.user, image_id=item.image_id, license_type=item.license_type, order_item=item
                    )
            except IntegrityError:
                logger.warning("Lisensi sudah dimiliki", extra={"order_item_id": item.pk})
        order.vendor_orders.update(status=VendorOrderStatus.COMPLETED, completed_at=now)
        order.status = OrderStatus.COMPLETED
        order.completed_at = now
    else:
        order.vendor_orders.update(status=VendorOrderStatus.NEW)
    order.save()

    def notify():
        send_templated_email(
            order.user.email, f"Pembayaran {order.order_number} diterima", "order_paid_customer", {"order": order}
        )
        for vendor_order in vendor_orders:
            send_templated_email(
                vendor_order.vendor.user.email,
                f"Pesanan baru {order.order_number}",
                "order_paid_vendor",
                {"vendor_order": vendor_order},
            )

    transaction.on_commit(notify)


def _close_unpaid_order(order: Order, status: str, reason: str = "") -> None:
    now = timezone.now()
    _restore_stock(order.items.all())
    order.vendor_orders.update(status=VendorOrderStatus.CANCELLED, cancelled_at=now)
    order.status = status
    order.cancelled_at = now
    order.cancel_reason = reason
    order.save()


# --- Kedaluwarsa -----------------------------------------------------------------
def expire_if_overdue(order: Order) -> Order:
    if order.status == OrderStatus.PENDING and order.expires_at <= timezone.now():
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            if locked.status == OrderStatus.PENDING:
                _close_unpaid_order(locked, OrderStatus.EXPIRED, reason="Batas waktu pembayaran terlewati.")
        order.refresh_from_db()
    return order


def expire_overdue_orders(batch_size: int = 100) -> int:
    count = 0
    while True:
        with transaction.atomic():
            batch = list(
                Order.objects.select_for_update(skip_locked=True)
                .filter(status=OrderStatus.PENDING, expires_at__lte=timezone.now())
                .order_by("expires_at")[:batch_size]
            )
            for order in batch:
                _close_unpaid_order(order, OrderStatus.EXPIRED, reason="Batas waktu pembayaran terlewati.")
        count += len(batch)
        if len(batch) < batch_size:
            return count


# --- Pembatalan oleh customer ----------------------------------------------------
def cancellation_block_reason(order: Order) -> tuple[str, str] | None:
    if order.status == OrderStatus.PENDING:
        return None
    if order.order_type == ItemType.IMAGE and order.status in (OrderStatus.PAID, OrderStatus.COMPLETED):
        return "digital_goods_non_refundable", "Aset digital yang sudah dibayar tidak dapat dibatalkan."
    if order.status == OrderStatus.PAID and all(vo.status == VendorOrderStatus.NEW for vo in order.vendor_orders.all()):
        return None
    return "order_not_cancellable", "Pesanan tidak dapat dibatalkan pada status ini."


def cancel_order(order: Order, reason: str = "") -> Order:
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        block = cancellation_block_reason(order)
        if block:
            raise BusinessError(*block)
        was_paid = order.status == OrderStatus.PAID
        pending_payments = list(order.payments.filter(status=PaymentStatus.PENDING))
        _close_unpaid_order(order, OrderStatus.CANCELLED, reason=reason or "Dibatalkan pembeli.")
        if was_paid:
            order.refund_status = RefundStatus.PENDING
            order.save(update_fields=["refund_status", "updated_at"])

    for payment in pending_payments:
        get_provider(payment.provider).cancel(payment)
    return order


# --- Status sub-pesanan (vendor / customer) --------------------------------------
def transition_vendor_order(vendor_order: VendorOrder, new_status: str, *, actor: str, reason: str = "") -> VendorOrder:
    """actor: 'vendor' | 'customer' | 'admin'."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=vendor_order.order_id)
        vendor_order = (
            VendorOrder.objects.select_for_update()
            .select_related("vendor__user", "order__user")
            .get(pk=vendor_order.pk)
        )
        current = vendor_order.status
        allowed = VENDOR_TRANSITIONS.get(current, set())
        if actor == "customer":
            allowed = {VendorOrderStatus.COMPLETED} if current == VendorOrderStatus.SHIPPED else set()
        if actor != "admin" and new_status not in allowed:
            raise BusinessError("invalid_transition", f"Status tidak dapat diubah dari '{current}' ke '{new_status}'.")
        if new_status == VendorOrderStatus.CANCELLED and actor == "vendor" and not reason:
            raise exceptions.ValidationError({"reason": ["Alasan penolakan wajib diisi."]})

        vendor_order.status = new_status
        setattr(vendor_order, TIMESTAMP_FIELD[new_status], timezone.now())
        if new_status == VendorOrderStatus.CANCELLED:
            vendor_order.cancel_reason = reason
            _restore_stock(vendor_order.items.all())
            if order.status == OrderStatus.PAID:
                order.refund_status = RefundStatus.PENDING
        vendor_order.save()
        _sync_order_completion(order)

    if new_status in (VendorOrderStatus.SHIPPED, VendorOrderStatus.CANCELLED):
        send_templated_email(
            order.user.email,
            f"Update pesanan {order.order_number}",
            "vendor_order_status",
            {"vendor_order": vendor_order},
        )
    return vendor_order


def _sync_order_completion(order: Order) -> None:
    statuses = list(order.vendor_orders.values_list("status", flat=True))
    if order.status != OrderStatus.PAID or not all(s in FINAL_VENDOR_STATUSES for s in statuses):
        order.save()
        return
    now = timezone.now()
    if VendorOrderStatus.COMPLETED in statuses:
        order.status, order.completed_at = OrderStatus.COMPLETED, now
    else:
        order.status, order.cancelled_at = OrderStatus.CANCELLED, now
        order.cancel_reason = "Semua pesanan ditolak toko."
    order.save()
