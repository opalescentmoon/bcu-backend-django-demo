from rest_framework import serializers

from food.models import FoodVariant
from images.models import ImageLicenseOption
from users.models import Address

from .models import ItemType, Order, OrderItem, Payment, VendorOrder, VendorOrderStatus
from .services import cancellation_block_reason


def _file_url(field):
    return field.url if field else None


# --- Keranjang -------------------------------------------------------------------
class CartItemCreateSerializer(serializers.Serializer):
    item_type = serializers.ChoiceField(choices=ItemType.choices)
    food_variant_id = serializers.PrimaryKeyRelatedField(
        queryset=FoodVariant.objects.select_related("food__vendor"), required=False, source="food_variant"
    )
    image_license_id = serializers.PrimaryKeyRelatedField(
        queryset=ImageLicenseOption.objects.select_related("image__vendor"), required=False, source="image_license"
    )
    quantity = serializers.IntegerField(min_value=1, max_value=99, default=1)

    def validate(self, attrs):
        if attrs["item_type"] == ItemType.FOOD and not attrs.get("food_variant"):
            raise serializers.ValidationError({"food_variant_id": ["Wajib untuk item makanan."]})
        if attrs["item_type"] == ItemType.IMAGE and not attrs.get("image_license"):
            raise serializers.ValidationError({"image_license_id": ["Wajib untuk item gambar."]})
        return attrs


class CartItemUpdateSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1, max_value=99)


def cart_payload(summary) -> dict:
    """Ubah hasil orders.cart.summarize() menjadi JSON (docs/04 §3.6)."""

    def food_entry(entry):
        item, variant = entry["item"], entry["item"].food_variant
        return {
            "id": item.pk,
            "quantity": item.quantity,
            "variant": {"id": variant.pk, "name": variant.name, "price": str(variant.price), "stock": variant.stock},
            "food": {"id": variant.food_id, "title": variant.food.title, "image_url": _file_url(variant.food.image)},
            "line_total": str(entry["line_total"]),
            "is_valid": entry["is_valid"],
            "invalid_reason": entry["invalid_reason"],
        }

    def image_entry(entry):
        item, license_option = entry["item"], entry["item"].image_license
        image = license_option.image
        return {
            "id": item.pk,
            "license": {
                "id": license_option.pk,
                "license_type": license_option.license_type,
                "price": str(license_option.price),
            },
            "image": {
                "id": image.pk,
                "title": image.title,
                "thumbnail_url": _file_url(image.thumbnail),
                "vendor": {"store_name": image.vendor.store_name, "slug": image.vendor.slug},
            },
            "is_valid": entry["is_valid"],
            "invalid_reason": entry["invalid_reason"],
        }

    food = summary["food"]
    return {
        "food": {
            "groups": [
                {
                    "vendor": {
                        "store_name": g["vendor"].store_name,
                        "slug": g["vendor"].slug,
                        "city": g["vendor"].city,
                        "is_open": g["vendor"].is_open,
                        "delivery_fee": str(g["vendor"].delivery_fee),
                    },
                    "items": [food_entry(e) for e in g["items"]],
                    "subtotal": str(g["subtotal"]),
                }
                for g in food["groups"]
            ],
            "subtotal": str(food["subtotal"]),
            "delivery_fee_total": str(food["delivery_fee_total"]),
            "total": str(food["total"]),
        },
        "image": {
            "items": [image_entry(e) for e in summary["image"]["items"]],
            "total": str(summary["image"]["total"]),
        },
        "item_count": summary["item_count"],
    }


# --- Checkout & pembayaran -------------------------------------------------------
class CheckoutSerializer(serializers.Serializer):
    order_type = serializers.ChoiceField(choices=ItemType.choices)
    address_id = serializers.IntegerField(required=False)
    notes = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        address_id = attrs.pop("address_id", None)
        attrs["address"] = None
        if attrs["order_type"] == ItemType.FOOD:
            if address_id is None:
                raise serializers.ValidationError({"address_id": ["Alamat pengiriman wajib diisi."]})
            address = Address.objects.filter(pk=address_id, user=self.context["request"].user).first()
            if address is None:
                raise serializers.ValidationError({"address_id": ["Alamat tidak ditemukan."]})
            attrs["address"] = address
        return attrs


class PaymentSerializer(serializers.ModelSerializer):
    client_key = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = ["provider", "provider_order_id", "status", "snap_token", "redirect_url", "client_key", "created_at"]

    def get_client_key(self, obj) -> str:
        from django.conf import settings

        return settings.MIDTRANS_CLIENT_KEY if obj.provider == "midtrans" else ""


class SimulatePaymentSerializer(serializers.Serializer):
    result = serializers.ChoiceField(choices=["success", "failure", "expire"])


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


# --- Pesanan ---------------------------------------------------------------------
class OrderItemSerializer(serializers.ModelSerializer):
    title = serializers.CharField(source="title_snapshot")
    variant = serializers.CharField(source="variant_snapshot")
    image_url = serializers.SerializerMethodField()
    product_id = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product_type",
            "product_id",
            "title",
            "variant",
            "license_type",
            "unit_price",
            "quantity",
            "line_total",
            "image_url",
        ]

    def get_image_url(self, obj) -> str | None:
        if obj.product_type == ItemType.FOOD:
            return _file_url(obj.food.image)
        return _file_url(obj.image.thumbnail)

    def get_product_id(self, obj) -> int:
        return obj.food_id if obj.product_type == ItemType.FOOD else obj.image_id


def timeline(vendor_order: VendorOrder, order: Order) -> list[dict]:
    points = [
        (VendorOrderStatus.NEW, order.paid_at if vendor_order.status != VendorOrderStatus.AWAITING_PAYMENT else None),
        (VendorOrderStatus.PROCESSING, vendor_order.processing_at),
        (VendorOrderStatus.SHIPPED, vendor_order.shipped_at),
        (VendorOrderStatus.COMPLETED, vendor_order.completed_at),
        (VendorOrderStatus.CANCELLED, vendor_order.cancelled_at),
    ]
    return [{"status": status, "at": at} for status, at in points if at]


class VendorOrderInOrderSerializer(serializers.ModelSerializer):
    vendor = serializers.SerializerMethodField()
    items = OrderItemSerializer(many=True, read_only=True)
    timeline = serializers.SerializerMethodField()
    can_confirm_received = serializers.SerializerMethodField()

    class Meta:
        model = VendorOrder
        fields = [
            "id",
            "vendor",
            "status",
            "subtotal",
            "delivery_fee",
            "cancel_reason",
            "items",
            "timeline",
            "can_confirm_received",
        ]

    def get_vendor(self, obj) -> dict:
        return {"store_name": obj.vendor.store_name, "slug": obj.vendor.slug}

    def get_timeline(self, obj) -> list[dict]:
        return timeline(obj, obj.order)

    def get_can_confirm_received(self, obj) -> bool:
        return obj.status == VendorOrderStatus.SHIPPED


class OrderListSerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    vendor_orders = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "order_number",
            "order_type",
            "status",
            "refund_status",
            "total_amount",
            "item_count",
            "thumbnail_url",
            "vendor_orders",
            "created_at",
            "expires_at",
        ]

    def get_item_count(self, obj) -> int:
        return sum(item.quantity for item in obj.items.all())

    def get_thumbnail_url(self, obj) -> str | None:
        first = next(iter(obj.items.all()), None)
        return OrderItemSerializer().get_image_url(first) if first else None

    def get_vendor_orders(self, obj) -> list[dict]:
        return [{"store_name": vo.vendor.store_name, "status": vo.status} for vo in obj.vendor_orders.all()]


class OrderDetailSerializer(serializers.ModelSerializer):
    shipping = serializers.SerializerMethodField()
    vendor_orders = VendorOrderInOrderSerializer(many=True, read_only=True)
    can_cancel = serializers.SerializerMethodField()
    can_pay = serializers.SerializerMethodField()
    payment = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "order_number",
            "order_type",
            "status",
            "refund_status",
            "subtotal",
            "delivery_fee_total",
            "total_amount",
            "payment_method",
            "shipping",
            "notes",
            "cancel_reason",
            "created_at",
            "paid_at",
            "completed_at",
            "cancelled_at",
            "expires_at",
            "can_cancel",
            "can_pay",
            "payment",
            "vendor_orders",
        ]

    def get_shipping(self, obj) -> dict | None:
        if obj.order_type != ItemType.FOOD:
            return None
        return {"recipient": obj.shipping_recipient, "phone": obj.shipping_phone, "address": obj.shipping_address}

    def get_can_cancel(self, obj) -> bool:
        return obj.status in ("pending", "paid") and cancellation_block_reason(obj) is None

    def get_can_pay(self, obj) -> bool:
        from django.utils import timezone

        return obj.status == "pending" and obj.expires_at > timezone.now()

    def get_payment(self, obj) -> dict | None:
        payment = next(iter(sorted(obj.payments.all(), key=lambda p: p.created_at, reverse=True)), None)
        return PaymentSerializer(payment).data if payment else None


class VendorOrderSerializer(serializers.ModelSerializer):
    """Tampilan vendor: data pembeli hanya untuk pesanan makanan (docs/07 §6)."""

    order_number = serializers.CharField(source="order.order_number")
    order_type = serializers.CharField(source="order.order_type")
    order_status = serializers.CharField(source="order.status")
    customer = serializers.SerializerMethodField()
    notes = serializers.CharField(source="order.notes")
    items = OrderItemSerializer(many=True, read_only=True)
    timeline = serializers.SerializerMethodField()
    allowed_transitions = serializers.SerializerMethodField()

    class Meta:
        model = VendorOrder
        fields = [
            "id",
            "order_number",
            "order_type",
            "order_status",
            "status",
            "subtotal",
            "delivery_fee",
            "commission_amount",
            "vendor_earning",
            "payout_status",
            "cancel_reason",
            "customer",
            "notes",
            "items",
            "timeline",
            "allowed_transitions",
            "created_at",
        ]

    def get_customer(self, obj) -> dict:
        order = obj.order
        if order.order_type != ItemType.FOOD:
            return {"username": order.user.username}
        return {
            "username": order.user.username,
            "recipient": order.shipping_recipient,
            "phone": order.shipping_phone,
            "address": order.shipping_address,
        }

    def get_timeline(self, obj) -> list[dict]:
        return timeline(obj, obj.order)

    def get_allowed_transitions(self, obj) -> list[str]:
        from .services import VENDOR_TRANSITIONS

        return sorted(VENDOR_TRANSITIONS.get(obj.status, set()))


class VendorOrderStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            VendorOrderStatus.PROCESSING,
            VendorOrderStatus.SHIPPED,
            VendorOrderStatus.COMPLETED,
            VendorOrderStatus.CANCELLED,
        ]
    )
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
