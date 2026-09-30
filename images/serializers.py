import json
from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from catalog.models import Category, CategoryType
from catalog.serializers import CategoryBriefSerializer
from core.validators import validate_image_file
from users.models import VendorProfile

from .models import LICENSE_RANK, ImageLicenseOption, ImageProduct, ImagePurchase, LicenseType
from .services import validate_license_prices

MAX_TAGS = 10


class VendorInImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorProfile
        fields = ["store_name", "slug"]


class LicenseOptionSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("1"))

    class Meta:
        model = ImageLicenseOption
        fields = ["id", "license_type", "price", "is_active"]
        read_only_fields = ["id"]


class ImageListSerializer(serializers.ModelSerializer):
    thumbnail_url = serializers.SerializerMethodField()
    category = CategoryBriefSerializer(read_only=True)
    vendor = VendorInImageSerializer(read_only=True)

    class Meta:
        model = ImageProduct
        fields = [
            "id",
            "title",
            "slug",
            "thumbnail_url",
            "min_price",
            "width",
            "height",
            "orientation",
            "category",
            "vendor",
        ]

    def get_thumbnail_url(self, obj) -> str | None:
        return obj.thumbnail.url if obj.thumbnail else None


class ImageDetailSerializer(ImageListSerializer):
    preview_url = serializers.SerializerMethodField()
    licenses = serializers.SerializerMethodField()
    purchased_licenses = serializers.SerializerMethodField()

    class Meta(ImageListSerializer.Meta):
        fields = [
            *ImageListSerializer.Meta.fields,
            "description",
            "tags",
            "preview_url",
            "file_format",
            "file_size",
            "licenses",
            "purchased_licenses",
            "created_at",
        ]

    def get_preview_url(self, obj) -> str | None:
        return obj.watermarked_preview.url if obj.watermarked_preview else None

    def get_licenses(self, obj) -> list[dict]:
        licenses = obj.licenses.all()
        if not self.context.get("is_owner"):
            licenses = [lic for lic in licenses if lic.is_active]
        return LicenseOptionSerializer(licenses, many=True).data

    def get_purchased_licenses(self, obj) -> list[str]:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return []
        owned = ImagePurchase.objects.filter(user=request.user, image=obj).values_list("license_type", flat=True)
        return sorted(owned, key=lambda t: LICENSE_RANK[t])


class OwnerImageSerializer(ImageDetailSerializer):
    """Tampilan vendor pemilik: termasuk status moderasi."""

    class Meta(ImageDetailSerializer.Meta):
        fields = [*ImageDetailSerializer.Meta.fields, "status", "rejection_reason", "is_deleted", "updated_at"]


@extend_schema_field({"type": "array", "items": {"type": "string"}})
class TagsField(serializers.Field):
    def to_internal_value(self, data):
        if isinstance(data, str):
            data = [t for t in (part.strip().lstrip("#").lower() for part in data.split(",")) if t]
        if not isinstance(data, list) or not all(isinstance(t, str) for t in data):
            raise serializers.ValidationError("Format tag tidak valid.")
        data = list(dict.fromkeys(t[:50] for t in data))
        if len(data) > MAX_TAGS:
            raise serializers.ValidationError(f"Maksimal {MAX_TAGS} tag.")
        return data

    def to_representation(self, value):
        return value


class ImageWriteSerializer(serializers.Serializer):
    title = serializers.CharField(min_length=3, max_length=150)
    description = serializers.CharField(required=False, allow_blank=True)
    category_id = serializers.PrimaryKeyRelatedField(
        source="category", queryset=Category.objects.filter(type=CategoryType.IMAGE, is_active=True)
    )
    tags = TagsField(required=False)
    file = serializers.FileField(help_text="JPG/PNG/WebP, maks 25 MB, sisi terpendek ≥ 1000 px")
    licenses = serializers.JSONField(help_text='JSON: [{"license_type":"personal","price":"50000"}]')

    def validate_file(self, value):
        fmt, width, height = validate_image_file(value, max_mb=25, min_side=1000)
        self._file_meta = {"format": fmt, "width": width, "height": height}
        return value

    def validate_licenses(self, value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError("Format lisensi harus JSON.") from exc
        if not isinstance(value, list) or not value:
            raise serializers.ValidationError("Minimal satu lisensi.")
        serializer = LicenseOptionSerializer(data=value, many=True)
        serializer.is_valid(raise_exception=True)
        types = [item["license_type"] for item in serializer.validated_data]
        if len(types) != len(set(types)):
            raise serializers.ValidationError("Jenis lisensi tidak boleh ganda.")
        try:
            validate_license_prices(serializer.validated_data)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return serializer.validated_data

    def validated_payload(self):
        data = dict(self.validated_data)
        if "file" in data:
            data["meta"] = self._file_meta
        return data


class ImagePurchaseSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    order_number = serializers.CharField(source="order_item.order.order_number")

    class Meta:
        model = ImagePurchase
        fields = ["id", "image", "license_type", "order_number", "purchased_at", "download_count", "last_downloaded_at"]

    def get_image(self, obj) -> dict:
        return {
            "id": obj.image_id,
            "title": obj.image.title,
            "thumbnail_url": obj.image.thumbnail.url if obj.image.thumbnail else None,
            "width": obj.image.width,
            "height": obj.image.height,
            "vendor": {"store_name": obj.image.vendor.store_name, "slug": obj.image.vendor.slug},
        }


class DownloadLinkSerializer(serializers.Serializer):
    download_url = serializers.CharField()
    expires_at = serializers.DateTimeField()
    filename = serializers.CharField()
    license_type = serializers.ChoiceField(choices=LicenseType.choices)
