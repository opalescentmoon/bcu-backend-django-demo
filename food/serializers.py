import json
from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from catalog.models import Category, CategoryType
from catalog.serializers import CategoryBriefSerializer
from core.imaging import resize_upload_to_jpeg
from core.validators import validate_image_file
from users.models import VendorProfile

from .models import FoodProduct, FoodVariant


class VendorInFoodSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorProfile
        fields = ["store_name", "slug", "city", "is_open"]


class VendorInFoodDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorProfile
        fields = ["store_name", "slug", "city", "is_open", "delivery_fee"]


class FoodVariantSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("1"))
    stock = serializers.IntegerField(min_value=0)

    class Meta:
        model = FoodVariant
        fields = ["id", "name", "price", "stock", "is_active", "sort_order"]
        read_only_fields = ["id"]


class FoodListSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    in_stock = serializers.SerializerMethodField()
    category = CategoryBriefSerializer(read_only=True)
    vendor = VendorInFoodSerializer(read_only=True)

    class Meta:
        model = FoodProduct
        fields = ["id", "title", "slug", "image_url", "min_price", "is_available", "in_stock", "category", "vendor"]

    def get_image_url(self, obj) -> str | None:
        return obj.image.url if obj.image else None

    def get_in_stock(self, obj) -> bool:
        total = getattr(obj, "total_stock", None)
        if total is None:
            total = sum(v.stock for v in obj.variants.all() if v.is_active)
        return bool(total)


class FoodDetailSerializer(FoodListSerializer):
    vendor = VendorInFoodDetailSerializer(read_only=True)
    variants = serializers.SerializerMethodField()

    class Meta(FoodListSerializer.Meta):
        fields = [*FoodListSerializer.Meta.fields, "description", "variants", "created_at", "updated_at"]

    def get_variants(self, obj) -> list[dict]:
        variants = obj.variants.all()
        if not self.context.get("include_inactive_variants"):
            variants = [v for v in variants if v.is_active]
        return FoodVariantSerializer(variants, many=True).data


class FoodWriteSerializer(serializers.ModelSerializer):
    category_id = serializers.PrimaryKeyRelatedField(
        source="category", queryset=Category.objects.filter(type=CategoryType.FOOD, is_active=True)
    )
    image = serializers.ImageField(required=True)
    variants = serializers.JSONField(write_only=True, required=False, help_text='JSON: [{"name","price","stock"}]')

    class Meta:
        model = FoodProduct
        fields = ["title", "description", "category_id", "image", "is_available", "variants"]
        extra_kwargs = {"title": {"min_length": 3}}

    def validate_image(self, value):
        validate_image_file(value, max_mb=5, max_side=4000)
        return resize_upload_to_jpeg(value, max_side=1200)

    def validate_variants(self, value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError("Format varian harus JSON.") from exc
        if not isinstance(value, list) or not value:
            raise serializers.ValidationError("Minimal satu varian.")
        serializer = FoodVariantSerializer(data=value, many=True)
        serializer.is_valid(raise_exception=True)
        names = [v["name"].strip().lower() for v in serializer.validated_data]
        if len(names) != len(set(names)):
            raise serializers.ValidationError("Nama varian tidak boleh sama.")
        if not any(v.get("is_active", True) for v in serializer.validated_data):
            raise serializers.ValidationError("Minimal satu varian aktif.")
        return serializer.validated_data

    def validate(self, attrs):
        if self.instance is None and "variants" not in attrs:
            raise serializers.ValidationError({"variants": ["Minimal satu varian."]})
        if self.instance is not None:
            attrs.pop("variants", None)  # varian diubah lewat endpoint varian
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        variants = validated_data.pop("variants")
        food = FoodProduct.objects.create(vendor=self.context["vendor"], **validated_data)
        FoodVariant.objects.bulk_create([FoodVariant(food=food, **v) for v in variants])
        food.refresh_min_price()
        return food
