from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.permissions import AllowAny

from .models import Category
from .serializers import CategorySerializer


@extend_schema(tags=["katalog"], parameters=[OpenApiParameter("type", str, enum=["food", "image"])])
class CategoryListView(generics.ListAPIView):
    serializer_class = CategorySerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        qs = Category.objects.filter(is_active=True)
        category_type = self.request.query_params.get("type")
        if category_type:
            qs = qs.filter(type=category_type)
        return qs
