"""Factory & helper untuk test (dipakai lewat conftest.py)."""

import hashlib
import io
from decimal import Decimal

import factory
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from catalog.models import Category
from food.models import FoodProduct, FoodVariant
from users.models import Address, Role, User, VendorProfile, VendorStatus


def make_image_bytes(width=1600, height=1200, fmt="JPEG", color=(200, 120, 40)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format=fmt)
    return buffer.getvalue()


def image_upload(name="photo.jpg", width=1600, height=1200, fmt="JPEG") -> SimpleUploadedFile:
    content_type = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[fmt]
    return SimpleUploadedFile(name, make_image_bytes(width, height, fmt), content_type=content_type)


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    username = factory.Sequence(lambda n: f"user{n}")
    role = Role.CUSTOMER
    is_email_verified = True
    password = factory.PostGenerationMethodCall("set_password", "RahasiaKuat123")

    @factory.post_generation
    def save_after(obj, create, extracted, **kwargs):
        if create:
            obj.save()


class VendorProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VendorProfile

    user = factory.SubFactory(UserFactory, role=Role.VENDOR)
    store_name = factory.Sequence(lambda n: f"Toko {n}")
    phone = "081234567890"
    address = "Jl. Contoh No. 1"
    city = "Bandung"
    sells_food = True
    sells_images = True
    delivery_fee = Decimal("10000")
    status = VendorStatus.APPROVED


class AddressFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Address

    user = factory.SubFactory(UserFactory)
    label = "Rumah"
    recipient_name = "Budi"
    phone = "081200000000"
    street = "Jl. Melati No. 5"
    city = "Bandung"
    province = "Jawa Barat"
    postal_code = "40115"
    is_default = True


def food_category():
    return Category.objects.get_or_create(type="food", slug="makanan-berat", defaults={"name": "Makanan Berat"})[0]


def image_category():
    return Category.objects.get_or_create(
        type="image", slug="fotografi-makanan", defaults={"name": "Fotografi Makanan"}
    )[0]


def make_food(vendor, title="Nasi Goreng", variants=(("Reguler", "20000", 10),), **kwargs):
    food = FoodProduct.objects.create(
        vendor=vendor,
        category=kwargs.pop("category", None) or food_category(),
        title=title,
        image=SimpleUploadedFile("f.jpg", make_image_bytes(400, 300), content_type="image/jpeg"),
        **kwargs,
    )
    for name, price, stock in variants:
        FoodVariant.objects.create(food=food, name=name, price=Decimal(price), stock=stock)
    food.refresh_min_price()
    return food


def make_image_product(
    vendor, title="Rendang Flat Lay", licenses=(("personal", "50000"), ("commercial", "150000")), status="published"
):
    from images.models import ImageLicenseOption, ImageProduct

    raw = make_image_bytes(1200, 1000)
    image = ImageProduct.objects.create(
        vendor=vendor,
        category=image_category(),
        title=title,
        original_file=SimpleUploadedFile("orig.jpg", raw, content_type="image/jpeg"),
        watermarked_preview=SimpleUploadedFile("p.jpg", make_image_bytes(600, 500), content_type="image/jpeg"),
        thumbnail=SimpleUploadedFile("t.jpg", make_image_bytes(400, 333), content_type="image/jpeg"),
        width=1200,
        height=1000,
        file_size=len(raw),
        file_format="jpeg",
        checksum_sha256=hashlib.sha256(raw).hexdigest(),
        status=status,
    )
    for license_type, price in licenses:
        ImageLicenseOption.objects.create(image=image, license_type=license_type, price=Decimal(price))
    image.refresh_min_price()
    return image


def make_image_purchase(user, image, license_type="personal"):
    """Buat hak unduh lengkap dengan order lunas (untuk test modul gambar)."""
    import uuid
    from datetime import timedelta

    from django.utils import timezone

    from images.models import ImagePurchase
    from orders.models import Order, OrderItem, VendorOrder

    price = image.licenses.get(license_type=license_type).price
    order = Order.objects.create(
        order_number=f"IM-TEST-{uuid.uuid4().hex[:6].upper()}",
        user=user,
        order_type="image",
        status="completed",
        subtotal=price,
        total_amount=price,
        expires_at=timezone.now() + timedelta(hours=1),
        paid_at=timezone.now(),
    )
    vendor_order = VendorOrder.objects.create(
        order=order,
        vendor=image.vendor,
        status="completed",
        subtotal=price,
        commission_rate=Decimal("0.1"),
        commission_amount=price / 10,
        vendor_earning=price - price / 10,
    )
    item = OrderItem.objects.create(
        order=order,
        vendor_order=vendor_order,
        product_type="image",
        image=image,
        license_type=license_type,
        title_snapshot=image.title,
        unit_price=price,
        quantity=1,
        line_total=price,
    )
    return ImagePurchase.objects.create(user=user, image=image, license_type=license_type, order_item=item)
