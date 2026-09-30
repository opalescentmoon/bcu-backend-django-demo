import uuid
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from django.utils.deconstruct import deconstructible
from django.utils.text import slugify


@deconstructible
class UUIDPath:
    """upload_to yang mengganti nama file dengan UUID (docs/07 §5)."""

    def __init__(self, prefix):
        self.prefix = prefix

    def __call__(self, instance, filename):
        return f"{self.prefix}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"

    def __eq__(self, other):
        return isinstance(other, UUIDPath) and other.prefix == self.prefix


def round_rupiah(value) -> Decimal:
    return Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP).quantize(Decimal("0.01"))


def unique_slug(model, value, max_length=160, **scope):
    base = slugify(value)[: max_length - 10] or "item"
    slug, n = base, 2
    while model.objects.filter(slug=slug, **scope).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug
