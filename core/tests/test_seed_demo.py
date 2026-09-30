import pytest
from django.core.management import call_command

from food.models import FoodProduct
from images.models import ImageProduct, ImagePurchase
from orders.models import Order
from users.models import User


@pytest.mark.django_db
def test_seed_demo_minimal_and_reset():
    call_command("seed_demo", minimal=True)
    assert User.objects.filter(email="admin@demo.local", is_staff=True).exists()
    assert FoodProduct.objects.count() == 9
    assert ImageProduct.objects.public().exists()
    statuses = set(Order.objects.values_list("status", flat=True))
    assert {"completed", "paid", "pending"} <= statuses
    assert ImagePurchase.objects.filter(user__email="budi@demo.local").exists()

    call_command("seed_demo")  # sudah ada → tidak mengisi ulang
    assert FoodProduct.objects.count() == 9

    call_command("seed_demo", minimal=True, reset=True)
    assert FoodProduct.objects.count() == 9
    assert User.objects.filter(email="admin@demo.local").count() == 1
