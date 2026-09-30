from django.db import transaction
from django.db.models import Prefetch, Q, Sum
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import exceptions, filters, generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.exceptions import BusinessError
from core.permissions import IsApprovedVendor

from .filters import FoodFilter
from .models import FoodProduct, FoodVariant
from .serializers import FoodDetailSerializer, FoodListSerializer, FoodVariantSerializer, FoodWriteSerializer


def _base_queryset():
    return (
        FoodProduct.objects.select_related("vendor", "category")
        .prefetch_related(Prefetch("variants", queryset=FoodVariant.objects.order_by("sort_order", "price", "id")))
        .annotate(total_stock=Sum("variants__stock", filter=Q(variants__is_active=True)))
    )


def _vendor_of(request):
    return getattr(request.user, "vendor_profile", None) if request.user.is_authenticated else None


@extend_schema(tags=["makanan"])
class FoodViewSet(viewsets.ModelViewSet):
    filterset_class = FoodFilter
    filter_backends = [*viewsets.ModelViewSet.filter_backends, filters.OrderingFilter]
    ordering_fields = ["created_at", "min_price", "title"]
    ordering = ["-created_at"]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ["get", "post", "patch", "delete"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        return [IsApprovedVendor()]

    def get_queryset(self):
        qs = _base_queryset()
        vendor = _vendor_of(self.request)
        if self.action == "list":
            return qs.public()
        if self.action == "retrieve":
            visible = Q(is_available=True, is_deleted=False, vendor__status="approved")
            if vendor:
                visible |= Q(vendor=vendor, is_deleted=False)
            return qs.filter(visible)
        # update / destroy / varian: hanya milik vendor sendiri (lainnya → 404)
        return qs.filter(vendor=vendor, is_deleted=False)

    def get_serializer_class(self):
        if self.action == "list":
            return FoodListSerializer
        if self.action in ("create", "partial_update", "update"):
            return FoodWriteSerializer
        return FoodDetailSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "vendor": _vendor_of(self.request)}

    def retrieve(self, request, *args, **kwargs):
        food = self.get_object()
        vendor = _vendor_of(request)
        is_owner = vendor is not None and food.vendor_id == vendor.pk
        ctx = {**self.get_serializer_context(), "include_inactive_variants": is_owner}
        return Response(FoodDetailSerializer(food, context=ctx).data)

    def _detail_response(self, food, status_code=status.HTTP_200_OK):
        food = _base_queryset().get(pk=food.pk)
        ctx = {**self.get_serializer_context(), "include_inactive_variants": True}
        return Response(FoodDetailSerializer(food, context=ctx).data, status=status_code)

    def create(self, request, *args, **kwargs):
        vendor = _vendor_of(request)
        if not vendor.sells_food:
            raise exceptions.PermissionDenied("Toko Anda tidak terdaftar sebagai penjual makanan.")
        serializer = FoodWriteSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        food = serializer.save()
        return self._detail_response(food, status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        food = self.get_object()
        serializer = FoodWriteSerializer(food, data=request.data, partial=True, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return self._detail_response(food)

    def perform_destroy(self, instance):
        if instance.order_items.exists():
            instance.is_deleted = True
            instance.is_available = False
            instance.save(update_fields=["is_deleted", "is_available", "updated_at"])
        else:
            instance.delete()

    @extend_schema(request=FoodVariantSerializer, responses={201: FoodDetailSerializer})
    @action(detail=True, methods=["post"], url_path="variants")
    def add_variant(self, request, pk=None):
        food = self.get_object()
        serializer = FoodVariantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if food.variants.filter(name__iexact=serializer.validated_data["name"]).exists():
            raise exceptions.ValidationError({"name": ["Nama varian sudah ada."]})
        serializer.save(food=food)
        food.refresh_min_price()
        return self._detail_response(food, status.HTTP_201_CREATED)


@extend_schema(tags=["makanan"])
class FoodVariantDetailView(generics.GenericAPIView):
    """PATCH/DELETE /api/foods/{id}/variants/{variant_id}/"""

    serializer_class = FoodVariantSerializer
    permission_classes = [IsApprovedVendor]

    def get_variant(self):
        vendor = _vendor_of(self.request)
        return get_object_or_404(
            FoodVariant.objects.select_related("food"),
            pk=self.kwargs["variant_id"],
            food_id=self.kwargs["pk"],
            food__vendor=vendor,
            food__is_deleted=False,
        )

    @staticmethod
    def _ensure_other_active(variant):
        if not variant.food.variants.filter(is_active=True).exclude(pk=variant.pk).exists():
            raise BusinessError("last_active_variant", "Produk harus memiliki minimal satu varian aktif.")

    @transaction.atomic
    def patch(self, request, pk, variant_id):
        variant = self.get_variant()
        serializer = FoodVariantSerializer(variant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data.get("is_active") is False and variant.is_active:
            self._ensure_other_active(variant)
        name = serializer.validated_data.get("name")
        if name and variant.food.variants.filter(name__iexact=name).exclude(pk=variant.pk).exists():
            raise exceptions.ValidationError({"name": ["Nama varian sudah ada."]})
        serializer.save()
        variant.food.refresh_min_price()
        return Response(FoodVariantSerializer(variant).data)

    @transaction.atomic
    def delete(self, request, pk, variant_id):
        variant = self.get_variant()
        if variant.is_active:
            self._ensure_other_active(variant)
        if variant.order_items.exists():
            variant.is_active = False
            variant.save(update_fields=["is_active"])
        else:
            variant.delete()
        variant.food.refresh_min_price()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["vendor"])
class VendorFoodListView(generics.ListAPIView):
    """GET /api/vendor/foods/ — semua produk milik vendor (termasuk yang tidak tersedia)."""

    queryset = FoodProduct.objects.none()  # hanya untuk introspeksi skema OpenAPI
    serializer_class = FoodDetailSerializer
    permission_classes = [IsApprovedVendor]

    def get_queryset(self):
        return (
            _base_queryset().filter(vendor=self.request.user.vendor_profile, is_deleted=False).order_by("-created_at")
        )

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "include_inactive_variants": True}
