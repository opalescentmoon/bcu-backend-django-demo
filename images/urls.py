from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("images", views.ImageViewSet, basename="image")

urlpatterns = [
    path("downloads/", views.MyDownloadsView.as_view(), name="my-downloads"),
    path("vendor/images/", views.VendorImageListView.as_view(), name="vendor-images"),
    *router.urls,
]
