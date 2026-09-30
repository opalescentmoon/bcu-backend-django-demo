import django_filters
from django.contrib.postgres.search import SearchQuery
from django.db.models import F, Q

from .models import ImageProduct, LicenseType


class ImageFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")
    category = django_filters.CharFilter(field_name="category__slug")
    vendor = django_filters.CharFilter(field_name="vendor__slug")
    license = django_filters.ChoiceFilter(choices=LicenseType.choices, method="filter_license")
    min_price = django_filters.NumberFilter(field_name="min_price", lookup_expr="gte")
    max_price = django_filters.NumberFilter(field_name="min_price", lookup_expr="lte")
    orientation = django_filters.ChoiceFilter(
        choices=[("landscape", "Landscape"), ("portrait", "Portrait"), ("square", "Square")],
        method="filter_orientation",
    )

    class Meta:
        model = ImageProduct
        fields = ["search", "category", "vendor", "license", "min_price", "max_price", "orientation"]

    def filter_search(self, queryset, name, value):
        value = value.strip().lower()
        if not value:
            return queryset
        query = SearchQuery(value, config="simple", search_type="websearch")
        return queryset.filter(Q(search_vector=query) | Q(title__icontains=value) | Q(tags__contains=[value]))

    def filter_license(self, queryset, name, value):
        return queryset.filter(licenses__license_type=value, licenses__is_active=True).distinct()

    def filter_orientation(self, queryset, name, value):
        return {
            "landscape": queryset.filter(width__gt=F("height")),
            "portrait": queryset.filter(width__lt=F("height")),
            "square": queryset.filter(width=F("height")),
        }[value]
