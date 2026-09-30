"""Data demo (docs/01 §7). Semua gambar dibuat otomatis dengan Pillow — bebas masalah lisensi."""

import io
import random
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from PIL import Image, ImageDraw, ImageFilter

from catalog.models import Category
from food.models import FoodProduct, FoodVariant
from images.models import ImageStatus
from images.services import create_image_product
from images.watermark import _font
from orders import services as order_services
from orders.cart import add_food, add_image
from orders.models import Order
from orders.payments import get_provider
from users.models import Address, Role, User, VendorProfile, VendorStatus

PASSWORD = "DemoPass123!"
PALETTES = [
    ((234, 88, 12), (253, 186, 116)),
    ((13, 148, 136), (153, 246, 228)),
    ((190, 18, 60), (253, 164, 175)),
    ((161, 98, 7), (253, 224, 71)),
    ((21, 128, 61), (187, 247, 208)),
    ((109, 40, 217), (221, 214, 254)),
    ((30, 64, 175), (191, 219, 254)),
]

VENDORS = [
    # email, username, store, city, food, images, fee, status
    ("warung.budi@demo.local", "warungbudi", "Warung Budi", "Bandung", True, False, "10000", VendorStatus.APPROVED),
    ("kedai.teh@demo.local", "kedaiteh", "Kedai Teh Nusantara", "Bandung", True, False, "8000", VendorStatus.APPROVED),
    ("lensa.rasa@demo.local", "lensarasa", "Lensa Rasa Studio", "Yogyakarta", False, True, "0", VendorStatus.APPROVED),
    (
        "dapur.kreatif@demo.local",
        "dapurkreatif",
        "Dapur Kreatif",
        "Jakarta",
        True,
        True,
        "12000",
        VendorStatus.APPROVED,
    ),
    ("toko.baru@demo.local", "tokobaru", "Toko Baru Menunggu", "Surabaya", True, False, "9000", VendorStatus.PENDING),
]

FOODS = {
    "Warung Budi": [
        ("Nasi Goreng Kampung", "makanan-berat", [("Reguler", 20000), ("Jumbo", 25000)]),
        ("Soto Ayam Lamongan", "makanan-berat", [("Reguler", 18000)]),
        ("Ayam Bakar Madu", "makanan-berat", [("1/4 Ekor", 22000), ("1/2 Ekor", 38000)]),
        ("Nasi Uduk Komplit", "makanan-berat", [("Reguler", 17000)]),
        ("Mie Goreng Jawa", "makanan-berat", [("Reguler", 19000), ("Pedas", 19000)]),
        ("Gado-Gado Bandung", "makanan-sehat", [("Reguler", 16000)]),
        ("Pisang Goreng Keju", "camilan", [("5 pcs", 12000), ("10 pcs", 22000)]),
        ("Tahu Crispy", "camilan", [("Reguler", 10000)]),
        ("Rendang Sapi", "makanan-berat", [("Porsi", 32000)]),
    ],
    "Kedai Teh Nusantara": [
        ("Es Teh Manis", "minuman", [("Reguler", 5000), ("Jumbo", 8000)]),
        ("Teh Tarik", "minuman", [("Panas", 12000), ("Dingin", 13000)]),
        ("Es Jeruk Peras", "minuman", [("Reguler", 9000)]),
        ("Kopi Susu Gula Aren", "minuman", [("Reguler", 18000), ("Large", 23000)]),
        ("Wedang Jahe", "minuman", [("Reguler", 10000)]),
        ("Roti Bakar Coklat", "kue-roti", [("Reguler", 15000)]),
        ("Martabak Mini", "kue-roti", [("Coklat", 14000), ("Keju", 16000)]),
        ("Kue Cubit", "kue-roti", [("10 pcs", 12000)]),
    ],
    "Dapur Kreatif": [
        ("Salad Buah Yoghurt", "makanan-sehat", [("Cup", 18000)]),
        ("Poke Bowl Salmon", "makanan-sehat", [("Reguler", 55000)]),
        ("Dimsum Ayam Frozen", "frozen-food", [("Isi 10", 35000), ("Isi 20", 65000)]),
        ("Nugget Sayur Frozen", "frozen-food", [("500 gr", 42000)]),
        ("Rawon Surabaya", "makanan-berat", [("Reguler", 28000)]),
        ("Brownies Panggang", "kue-roti", [("Loyang Kecil", 45000), ("Loyang Besar", 80000)]),
        ("Cireng Rujak", "camilan", [("Reguler", 12000)]),
        ("Smoothie Mangga", "minuman", [("Reguler", 20000)]),
    ],
}

IMAGES = {
    "Lensa Rasa Studio": [
        ("Rendang Daging Flat Lay", "fotografi-makanan", ["rendang", "padang", "flatlay"], (2400, 1600)),
        ("Sate Ayam Bakar Arang", "fotografi-makanan", ["sate", "bakar"], (2400, 1600)),
        ("Nasi Tumpeng Syukuran", "fotografi-makanan", ["tumpeng", "tradisional"], (1600, 2400)),
        ("Es Cendol Segar", "fotografi-makanan", ["cendol", "minuman"], (1600, 2000)),
        ("Bakso Kuah Panas", "fotografi-makanan", ["bakso", "kuah"], (2000, 2000)),
        ("Gudeg Jogja Komplit", "fotografi-makanan", ["gudeg", "jogja"], (2400, 1600)),
        ("Kopi Tubruk Pagi", "fotografi-makanan", ["kopi", "pagi"], (1600, 2400)),
        ("Pasar Tradisional", "fotografi-makanan", ["pasar", "sayur"], (2400, 1350)),
        ("Klepon Pandan", "fotografi-makanan", ["klepon", "jajanan"], (2000, 2000)),
        ("Soto Betawi Close Up", "fotografi-makanan", ["soto", "betawi"], (2400, 1600)),
        ("Ilustrasi Warung Kopi", "ilustrasi", ["warung", "kopi"], (2400, 1600)),
        ("Ilustrasi Nasi Padang", "ilustrasi", ["padang", "ilustrasi"], (2000, 2000)),
        ("Pola Rempah Nusantara", "ilustrasi", ["rempah", "pattern"], (2000, 2000)),
        ("Ikon Jajanan Pasar", "ilustrasi", ["ikon", "jajanan"], (2400, 1600)),
    ],
    "Dapur Kreatif": [
        ("Banner Promo Ramadan", "banner-promosi", ["ramadan", "promo"], (2400, 1000)),
        ("Banner Diskon Akhir Pekan", "banner-promosi", ["diskon", "weekend"], (2400, 1000)),
        ("Banner Menu Baru", "banner-promosi", ["menu", "baru"], (2400, 1000)),
        ("Banner Gratis Ongkir", "banner-promosi", ["ongkir", "promo"], (2400, 1000)),
        ("Brownies di Meja Kayu", "fotografi-makanan", ["brownies", "kue"], (2000, 2400)),
        ("Poke Bowl Warna-warni", "fotografi-makanan", ["poke", "sehat"], (2400, 1600)),
    ],
}


def _generated_image(title: str, size=(1200, 900), seed=0) -> bytes:
    rng = random.Random(seed)
    dark, light = rng.choice(PALETTES)
    width, height = size
    img = Image.new("RGB", size, light)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / height
        draw.line([(0, y), (width, y)], fill=tuple(int(light[i] * (1 - t) + dark[i] * t) for i in range(3)))
    for _ in range(14):
        r = rng.randint(min(size) // 12, min(size) // 4)
        x, y = rng.randint(0, width), rng.randint(0, height)
        color = tuple(min(255, c + rng.randint(-40, 60)) for c in rng.choice([dark, light]))
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color)
    img = img.filter(ImageFilter.GaussianBlur(radius=min(size) // 80))
    draw = ImageDraw.Draw(img)
    cx, cy, pr = width // 2, height // 2, min(size) // 3
    draw.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=(250, 250, 245), outline=(220, 220, 210), width=6)
    draw.ellipse([cx - pr * 2 // 3, cy - pr * 2 // 3, cx + pr * 2 // 3, cy + pr * 2 // 3], fill=dark)
    font = _font(max(28, min(size) // 14))
    box = draw.textbbox((0, 0), title, font=font)
    tx = (width - (box[2] - box[0])) // 2
    draw.rounded_rectangle(
        [tx - 24, height - 150 - 12, tx + box[2] - box[0] + 24, height - 150 + box[3] - box[1] + 24], 16, fill=(0, 0, 0)
    )
    draw.text((tx, height - 150), title, font=font, fill=(255, 255, 255))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


def _upload(name, data):
    return SimpleUploadedFile(name, data, content_type="image/jpeg")


class Command(BaseCommand):
    help = "Isi database dengan data demo (akun, produk, gambar, pesanan contoh)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Hapus data demo & E2E lama (*@demo.local, *@e2e.local) sebelum mengisi.",
        )
        parser.add_argument("--minimal", action="store_true", help="Data minimal (untuk test E2E).")

    def handle(self, *args, reset=False, minimal=False, **options):
        if User.objects.filter(email="admin@demo.local").exists():
            if not reset:
                self.stdout.write(self.style.WARNING("Data demo sudah ada. Gunakan --reset untuk mengisi ulang."))
                return
            self._reset()
        with transaction.atomic():
            self._seed(minimal)
        self._seed_orders()
        self.stdout.write(self.style.SUCCESS(f"Data demo siap. Password semua akun: {PASSWORD}"))
        for email, role in [
            ("admin@demo.local", "admin"),
            ("budi@demo.local", "customer"),
            ("warung.budi@demo.local", "vendor makanan"),
            ("lensa.rasa@demo.local", "vendor gambar"),
        ]:
            self.stdout.write(f"  {role:<15} {email}")

    def _reset(self):
        from images.models import ImageProduct, ImagePurchase

        demo_users = User.objects.filter(Q(email__endswith="@demo.local") | Q(email__endswith="@e2e.local"))
        # Urutan penting karena relasi PROTECT: hak unduh → pesanan → produk → pengguna.
        ImagePurchase.objects.filter(user__in=demo_users).delete()
        Order.objects.filter(user__in=demo_users).delete()
        for image in ImageProduct.objects.filter(vendor__user__in=demo_users):
            for field in (image.original_file, image.watermarked_preview, image.thumbnail):
                field.storage.delete(field.name)
        ImageProduct.objects.filter(vendor__user__in=demo_users).delete()
        FoodProduct.objects.filter(vendor__user__in=demo_users).delete()
        demo_users.delete()
        self.stdout.write("Data demo lama dihapus.")

    def _user(self, email, username, role, **extra):
        user = User.objects.create_user(
            email=email, username=username, password=PASSWORD, role=role, is_email_verified=True, **extra
        )
        return user

    def _seed(self, minimal):
        self._user("admin@demo.local", "admin", Role.ADMIN, is_staff=True, is_superuser=True, first_name="Admin")
        customers = [
            self._user("budi@demo.local", "budi", Role.CUSTOMER, first_name="Budi", phone="081211110001"),
            self._user("siti@demo.local", "siti", Role.CUSTOMER, first_name="Siti", phone="081211110002"),
            self._user("andi@demo.local", "andi", Role.CUSTOMER, first_name="Andi", phone="081211110003"),
        ]
        for i, customer in enumerate(customers):
            Address.objects.create(
                user=customer,
                label="Rumah",
                recipient_name=customer.first_name,
                phone=customer.phone,
                street=f"Jl. Melati No. {i + 5}",
                city="Bandung",
                province="Jawa Barat",
                postal_code="40115",
                is_default=True,
            )
        Address.objects.create(
            user=customers[0],
            label="Kantor",
            recipient_name="Budi",
            phone=customers[0].phone,
            street="Jl. Asia Afrika No. 8",
            city="Bandung",
            province="Jawa Barat",
            postal_code="40111",
        )

        vendors = {}
        for email, username, store, city, food, images, fee, status in VENDORS:
            user = self._user(email, username, Role.VENDOR, phone="0812000000")
            logo = _upload("logo.jpg", _generated_image(store[:2].upper(), (400, 400), seed=hash(store) % 1000))
            vendors[store] = VendorProfile.objects.create(
                user=user,
                store_name=store,
                city=city,
                address=f"Jl. {store} No. 1, {city}",
                phone="0812000000",
                description=f"{store} — menyajikan yang terbaik dari {city}.",
                sells_food=food,
                sells_images=images,
                delivery_fee=Decimal(fee),
                status=status,
                logo=logo,
            )

        categories = {c.slug: c for c in Category.objects.all()}
        rng = random.Random(42)
        for store, items in FOODS.items():
            for idx, (title, cat, variants) in enumerate(items[:3] if minimal else items):
                food = FoodProduct.objects.create(
                    vendor=vendors[store],
                    category=categories[cat],
                    title=title,
                    description=f"{title} buatan {store}. Dimasak segar setiap hari dengan bahan pilihan.",
                    image=_upload("food.jpg", _generated_image(title, (1200, 900), seed=idx + hash(title) % 997)),
                )
                for order, (name, price) in enumerate(variants):
                    FoodVariant.objects.create(
                        food=food, name=name, price=Decimal(price), stock=rng.randint(5, 40), sort_order=order
                    )
                food.refresh_min_price()

        for store, items in IMAGES.items():
            for idx, (title, cat, tags, size) in enumerate(items[:3] if minimal else items):
                raw = _generated_image(title, size, seed=1000 + idx + hash(title) % 997)
                personal = Decimal(rng.choice([35000, 50000, 75000]))
                image = create_image_product(
                    vendors[store],
                    file=_upload(f"{tags[0]}.jpg", raw),
                    meta={"format": "jpeg", "width": size[0], "height": size[1]},
                    licenses=[
                        {"license_type": "personal", "price": personal},
                        {"license_type": "commercial", "price": personal * 3},
                    ],
                    title=title,
                    description=f"{title}. Resolusi tinggi, cocok untuk menu, media sosial, dan promosi.",
                    category=categories[cat],
                    tags=tags,
                )
                image.status = ImageStatus.PENDING_REVIEW if idx == len(items) - 1 else ImageStatus.PUBLISHED
                image.save(update_fields=["status"])

    def _seed_orders(self):
        """Pesanan contoh di berbagai status lewat service asli (bukan insert langsung)."""
        budi, siti = User.objects.get(email="budi@demo.local"), User.objects.get(email="siti@demo.local")
        simulation = get_provider("simulation")

        def pay(order, result="success"):
            payment = order_services.start_payment(order) if not order.payments.exists() else order.payments.first()
            order_services.apply_payment_notification(
                simulation.parse_notification(
                    {"provider_order_id": payment.provider_order_id, "result": result, "gross_amount": payment.amount}
                )
            )

        foods = list(FoodProduct.objects.select_related("vendor").prefetch_related("variants").order_by("id"))
        if len(foods) < 4:
            return

        def food_order(user, picks):
            for food, qty in picks:
                add_food(user, food.variants.first(), qty)
            return order_services.checkout(
                user, "food", address=user.addresses.get(is_default=True), notes="Tidak pedas ya"
            )

        # 1) Selesai
        completed = food_order(budi, [(foods[0], 2), (foods[-1], 1)])
        pay(completed)
        for vo in completed.vendor_orders.all():
            for status in ("processing", "shipped", "completed"):
                order_services.transition_vendor_order(vo, status, actor="vendor")
        # 2) Sedang diproses
        processing = food_order(siti, [(foods[1], 1)])
        pay(processing)
        order_services.transition_vendor_order(processing.vendor_orders.first(), "processing", actor="vendor")
        # 3) Baru dibayar (perlu aksi vendor)
        paid = food_order(budi, [(foods[2], 1), (foods[3], 2)])
        pay(paid)
        # 4) Menunggu pembayaran
        food_order(siti, [(foods[0], 1)])

        # 5) Aset gambar lunas
        from images.models import ImageProduct

        images = list(ImageProduct.objects.public().prefetch_related("licenses")[:2])
        for image, license_type in zip(images, ("commercial", "personal"), strict=False):
            add_image(budi, image.licenses.get(license_type=license_type))
        if images:
            pay(order_services.checkout(budi, "image"))
