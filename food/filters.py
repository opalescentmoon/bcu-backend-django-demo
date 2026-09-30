import django_filters
from django.contrib.postgres.search import SearchQuery
from django.db.models import Q

from .models import FoodProduct


class FoodFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")
    category = django_filters.CharFilter(field_name="category__slug")
    vendor = django_filters.CharFilter(field_name="vendor__slug")
    city = django_filters.CharFilter(field_name="vendor__city", lookup_expr="iexact")
    min_price = django_filters.NumberFilter(field_name="min_price", lookup_expr="gte")
    max_price = django_filters.NumberFilter(field_name="min_price", lookup_expr="lte")

    class Meta:
        model = FoodProduct
        fields = ["search", "category", "vendor", "city", "min_price", "max_price"]

    def filter_search(self, queryset, name, value):
        value = value.strip()
        if not value:
            return queryset
        query = SearchQuery(value, config="simple", search_type="websearch")
        return queryset.filter(Q(search_vector=query) | Q(title__icontains=value))
