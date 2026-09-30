import logging
from datetime import timedelta

from django.db.models import F, Prefetch
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import exceptions, filters, generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from core.models import PlatformSetting
from core.pagination import GalleryPagination
from core.permissions import IsApprovedVendor
from core.storage import private_download_url

from . import services
from .filters import ImageFilter
from .models import LICENSE_RANK, DownloadLog, ImageLicenseOption, ImageProduct, ImagePurchase
from .serializers import (
    DownloadLinkSerializer,
    ImageDetailSerializer,
    ImageListSerializer,
    ImagePurchaseSerializer,
    ImageWriteSerializer,
    OwnerImageSerializer,
)

logger = logging.getLogger(__name__)


def _base_queryset():
    return ImageProduct.objects.select_related("vendor", "category").prefetch_related(
        Prefetch("licenses", queryset=ImageLicenseOption.objects.order_by("price"))
    )


def _vendor_of(request):
    return getattr(request.user, "vendor_profile", None) if request.user.is_authenticated else None


def _client_ip(request):
    return request.META.get("REMOTE_ADDR")


@extend_schema(tags=["gambar"])
class ImageViewSet(viewsets.ModelViewSet):
    filterset_class = ImageFilter
    filter_backends = [*viewsets.ModelViewSet.filter_backends, filters.OrderingFilter]
    ordering_fields = ["created_at", "min_price", "title"]
    ordering = ["-created_at"]
    pagination_class = GalleryPagination
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ["get", "post", "patch", "delete"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        if self.action == "download":
            return [IsAuthenticated()]
        return [IsApprovedVendor()]

    def get_throttles(self):
        if self.action == "download":
            self.throttle_scope = "download"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        qs = _base_queryset()
        if self.action == "list":
            return qs.public()
        if self.action == "retrieve":
            vendor = _vendor_of(self.request)
            public = qs.public()
            return (public | qs.filter(vendor=vendor, is_deleted=False)).distinct() if vendor else public
        if self.action == "download":
            return qs  # termasuk gambar soft-deleted yang sudah dibeli (BR-08)
        return qs.filter(vendor=_vendor_of(self.request), is_deleted=False)

    def get_serializer_class(self):
        if self.action == "list":
            return ImageListSerializer
        if self.action in ("create", "partial_update"):
            return ImageWriteSerializer
        return ImageDetailSerializer

    def _owner_response(self, image, status_code=status.HTTP_200_OK):
        image = _base_queryset().get(pk=image.pk)
        ctx = {**self.get_serializer_context(), "is_owner": True}
        return Response(OwnerImageSerializer(image, context=ctx).data, status=status_code)

    def retrieve(self, request, *args, **kwargs):
        image = self.get_object()
        vendor = _vendor_of(request)
        if vendor and image.vendor_id == vendor.pk:
            return self._owner_response(image)
        return Response(ImageDetailSerializer(image, context=self.get_serializer_context()).data)

    @extend_schema(request={"multipart/form-data": ImageWriteSerializer}, responses={201: OwnerImageSerializer})
    def create(self, request, *args, **kwargs):
        vendor = _vendor_of(request)
        if not vendor.sells_images:
            raise exceptions.PermissionDenied("Toko Anda tidak terdaftar sebagai penjual aset gambar.")
        serializer = ImageWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        image = services.create_image_product(vendor, **serializer.validated_payload())
        return self._owner_response(image, status.HTTP_201_CREATED)

    @extend_schema(request={"multipart/form-data": ImageWriteSerializer}, responses={200: OwnerImageSerializer})
    def partial_update(self, request, *args, **kwargs):
        image = self.get_object()
        serializer = ImageWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        services.update_image_product(image, **serializer.validated_payload())
        return self._owner_response(image)

    def perform_destroy(self, instance):
        if instance.order_items.exists():
            instance.is_deleted = True
            instance.save(update_fields=["is_deleted", "updated_at"])
        else:
            files = (instance.original_file, instance.watermarked_preview, instance.thumbnail)
            instance.delete()
            services._delete_files(*files)

    @extend_schema(
        parameters=[OpenApiParameter("license", str, enum=["personal", "commercial"], required=False)],
        responses={200: DownloadLinkSerializer},
    )
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """Link unduhan sementara file original (hanya pembeli)."""
        purchases = ImagePurchase.objects.filter(user=request.user, image_id=pk).select_related("image")
        license_type = request.query_params.get("license")
        if license_type:
            purchases = purchases.filter(license_type=license_type)
        purchase = max(purchases, key=lambda p: LICENSE_RANK[p.license_type], default=None)
        if purchase is None:
            raise exceptions.PermissionDenied("Anda belum membeli lisensi untuk gambar ini.", code="not_purchased")

        image = purchase.image
        ttl = PlatformSetting.load().download_url_ttl_minutes * 60
        expires_at = timezone.now() + timedelta(seconds=ttl)
        url = private_download_url(image.original_file.name, image.download_filename, ttl, request)
        DownloadLog.objects.create(
            purchase=purchase,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:255],
            url_expires_at=expires_at,
        )
        ImagePurchase.objects.filter(pk=purchase.pk).update(
            download_count=F("download_count") + 1, last_downloaded_at=timezone.now()
        )
        logger.info("Link unduhan dibuat", extra={"purchase_id": purchase.pk, "user_id": request.user.pk})
        return Response(
            {
                "download_url": url,
                "expires_at": expires_at,
                "filename": image.download_filename,
                "license_type": purchase.license_type,
            }
        )


@extend_schema(tags=["gambar"])
class MyDownloadsView(generics.ListAPIView):
    """GET /api/downloads/ — aset gambar yang sudah dibeli."""

    queryset = ImagePurchase.objects.none()  # hanya untuk introspeksi skema OpenAPI
    serializer_class = ImagePurchaseSerializer

    def get_queryset(self):
        return ImagePurchase.objects.filter(user=self.request.user).select_related("image__vendor", "order_item__order")


@extend_schema(tags=["vendor"])
class VendorImageListView(generics.ListAPIView):
    queryset = ImageProduct.objects.none()  # hanya untuk introspeksi skema OpenAPI
    serializer_class = OwnerImageSerializer
    permission_classes = [IsApprovedVendor]

    def get_queryset(self):
        qs = _base_queryset().filter(vendor=self.request.user.vendor_profile, is_deleted=False)
        status_param = self.request.query_params.get("status")
        return qs.filter(status=status_param) if status_param else qs

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "is_owner": True}
