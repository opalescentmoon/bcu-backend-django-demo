from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from catalog.views import CategoryListView
from users.urls import auth_urlpatterns

from . import views

admin.site.site_header = f"{settings.APP_NAME} — Admin"
admin.site.site_title = settings.APP_NAME

api_urlpatterns = [
    path("auth/", include(auth_urlpatterns)),
    path("categories/", CategoryListView.as_view(), name="category-list"),
    path("health/", views.health, name="health"),
    path("files/<str:token>/", views.private_file, name="private-file"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("", include("users.urls")),
    path("", include("food.urls")),
    path("", include("images.urls")),
    path("", include("orders.urls")),
]

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/", include(api_urlpatterns)),
]

if settings.DEBUG and settings.STORAGE_BACKEND == "local":
    from django.conf.urls.static import static

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
