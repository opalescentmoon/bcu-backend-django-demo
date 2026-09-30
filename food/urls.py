from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("foods", views.FoodViewSet, basename="food")

urlpatterns = [
    path(
        "foods/<int:pk>/variants/<int:variant_id>/", views.FoodVariantDetailView.as_view(), name="food-variant-detail"
    ),
    path("vendor/foods/", views.VendorFoodListView.as_view(), name="vendor-foods"),
    *router.urls,
]
