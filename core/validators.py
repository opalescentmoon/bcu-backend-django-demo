from pathlib import Path

from PIL import Image, UnidentifiedImageError
from rest_framework import serializers

Image.MAX_IMAGE_PIXELS = 100_000_000  # proteksi decompression bomb (docs/07 §5)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_FORMATS = {"JPEG": "jpeg", "PNG": "png", "WEBP": "webp"}
MB = 1024 * 1024


def validate_image_file(file, *, max_mb, min_side=None, max_side=12000):
    """Validasi isi file gambar (bukan hanya ekstensi). Mengembalikan (format, width, height)."""
    if Path(file.name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise serializers.ValidationError("Format file harus JPG, PNG, atau WebP.")
    if file.size > max_mb * MB:
        raise serializers.ValidationError(f"Ukuran file maksimal {max_mb} MB.")
    try:
        file.seek(0)
        with Image.open(file) as img:
            img.verify()
        file.seek(0)
        with Image.open(file) as img:
            fmt, (width, height) = img.format, img.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise serializers.ValidationError("File bukan gambar yang valid.") from exc
    finally:
        file.seek(0)
    if fmt not in ALLOWED_FORMATS:
        raise serializers.ValidationError("Format file harus JPG, PNG, atau WebP.")
    if min_side and min(width, height) < min_side:
        raise serializers.ValidationError(f"Sisi terpendek gambar minimal {min_side} px.")
    if max(width, height) > max_side:
        raise serializers.ValidationError(f"Sisi terpanjang gambar maksimal {max_side} px.")
    return ALLOWED_FORMATS[fmt], width, height
