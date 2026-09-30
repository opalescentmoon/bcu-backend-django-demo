"""Logika keranjang: validasi item & penambahan (docs/04 §3.6)."""

from decimal import Decimal

from django.db import transaction

from core.exceptions import BusinessError
from food.models import FoodVariant
from images.models import LICENSE_RANK, ImageLicenseOption, ImagePurchase, ImageStatus
from users.models import VendorStatus

from .models import Cart, CartItem, ItemType

INVALID_MESSAGES = {
    "out_of_stock": "Stok habis.",
    "insufficient_stock": "Stok tidak mencukupi.",
    "unavailable": "Produk tidak tersedia.",
    "store_closed": "Toko sedang tutup.",
    "vendor_inactive": "Toko tidak aktif.",
    "already_purchased": "Anda sudah memiliki lisensi ini.",
    "own_product": "Anda tidak dapat membeli produk sendiri.",
}


def get_cart(user) -> Cart:
    cart, _ = Cart.objects.get_or_create(user=user)
    return cart


def cart_items(cart):
    return cart.items.select_related(
        "food_variant__food__vendor",
        "food_variant__food__category",
        "image_license__image__vendor",
    )


def owned_license_rank(user, image_id) -> int:
    types = ImagePurchase.objects.filter(user=user, image_id=image_id).values_list("license_type", flat=True)
    return max((LICENSE_RANK[t] for t in types), default=0)


def food_invalid_reason(variant: FoodVariant, quantity: int, user) -> str | None:
    food, vendor = variant.food, variant.food.vendor
    if vendor.user_id == user.pk:
        return "own_product"
    if vendor.status != VendorStatus.APPROVED:
        return "vendor_inactive"
    if not (variant.is_active and food.is_available and not food.is_deleted):
        return "unavailable"
    if not vendor.is_open:
        return "store_closed"
    if variant.stock <= 0:
        return "out_of_stock"
    if variant.stock < quantity:
        return "insufficient_stock"
    return None


def image_invalid_reason(license_option: ImageLicenseOption, user) -> str | None:
    image, vendor = license_option.image, license_option.image.vendor
    if vendor.user_id == user.pk:
        return "own_product"
    if vendor.status != VendorStatus.APPROVED:
        return "vendor_inactive"
    if not (license_option.is_active and image.status == ImageStatus.PUBLISHED and not image.is_deleted):
        return "unavailable"
    if owned_license_rank(user, image.pk) >= LICENSE_RANK[license_option.license_type]:
        return "already_purchased"
    return None


def item_invalid_reason(item: CartItem, user) -> str | None:
    if item.item_type == ItemType.FOOD:
        return food_invalid_reason(item.food_variant, item.quantity, user)
    return image_invalid_reason(item.image_license, user)


def _raise_invalid(reason):
    raise BusinessError(reason, INVALID_MESSAGES[reason])


@transaction.atomic
def add_food(user, variant: FoodVariant, quantity: int) -> CartItem:
    cart = get_cart(user)
    item = cart.items.select_for_update().filter(food_variant=variant).first()
    total = quantity + (item.quantity if item else 0)
    reason = food_invalid_reason(variant, total, user)
    if reason:
        _raise_invalid(reason)
    if item:
        item.quantity = total
        item.save(update_fields=["quantity"])
        return item
    return CartItem.objects.create(cart=cart, item_type=ItemType.FOOD, food_variant=variant, quantity=quantity)


@transaction.atomic
def add_image(user, license_option: ImageLicenseOption) -> CartItem:
    reason = image_invalid_reason(license_option, user)
    if reason:
        _raise_invalid(reason)
    cart = get_cart(user)
    # lisensi lain untuk gambar yang sama diganti (docs/04 §3.6)
    cart.items.filter(image_license__image_id=license_option.image_id).exclude(image_license=license_option).delete()
    item, _ = CartItem.objects.get_or_create(
        cart=cart, image_license=license_option, defaults={"item_type": ItemType.IMAGE, "quantity": 1}
    )
    return item


def update_quantity(item: CartItem, quantity: int, user) -> CartItem:
    if item.item_type != ItemType.FOOD:
        raise BusinessError("invalid_quantity", "Jumlah aset gambar selalu 1.", status_code=400)
    reason = food_invalid_reason(item.food_variant, quantity, user)
    if reason in ("insufficient_stock", "out_of_stock"):
        _raise_invalid(reason)
    item.quantity = quantity
    item.save(update_fields=["quantity"])
    return item


def summarize(cart, user) -> dict:
    """Struktur respons GET /api/cart/ (dipakai serializer)."""
    groups: dict[int, dict] = {}
    image_items = []
    for item in cart_items(cart):
        reason = item_invalid_reason(item, user)
        entry = {"item": item, "is_valid": reason is None, "invalid_reason": reason}
        if item.item_type == ItemType.FOOD:
            vendor = item.food_variant.food.vendor
            group = groups.setdefault(vendor.pk, {"vendor": vendor, "items": [], "subtotal": Decimal("0")})
            entry["line_total"] = item.food_variant.price * item.quantity
            group["items"].append(entry)
            if reason is None:
                group["subtotal"] += entry["line_total"]
        else:
            image_items.append(entry)

    food_groups = list(groups.values())
    food_subtotal = sum((g["subtotal"] for g in food_groups), Decimal("0"))
    delivery = sum((g["vendor"].delivery_fee for g in food_groups if g["subtotal"] > 0), Decimal("0"))
    image_total = sum((e["item"].image_license.price for e in image_items if e["is_valid"]), Decimal("0"))
    return {
        "food": {
            "groups": food_groups,
            "subtotal": food_subtotal,
            "delivery_fee_total": delivery,
            "total": food_subtotal + delivery,
        },
        "image": {"items": image_items, "total": image_total},
        "item_count": sum(len(g["items"]) for g in food_groups) + len(image_items),
    }
