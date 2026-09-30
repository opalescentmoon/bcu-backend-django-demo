from django.db import migrations

CATEGORIES = [
    ("food", "Makanan Berat", "makanan-berat", "bowl"),
    ("food", "Minuman", "minuman", "cup"),
    ("food", "Camilan", "camilan", "cookie"),
    ("food", "Kue & Roti", "kue-roti", "cake"),
    ("food", "Makanan Sehat", "makanan-sehat", "salad"),
    ("food", "Frozen Food", "frozen-food", "snowflake"),
    ("image", "Fotografi Makanan", "fotografi-makanan", "camera"),
    ("image", "Ilustrasi", "ilustrasi", "brush"),
    ("image", "Banner Promosi", "banner-promosi", "megaphone"),
]


def create_categories(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    for order, (type_, name, slug, icon) in enumerate(CATEGORIES):
        Category.objects.get_or_create(type=type_, slug=slug, defaults={"name": name, "icon": icon, "sort_order": order})


def remove_categories(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Category.objects.filter(slug__in=[c[2] for c in CATEGORIES]).delete()


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]
    operations = [migrations.RunPython(create_categories, remove_categories)]
