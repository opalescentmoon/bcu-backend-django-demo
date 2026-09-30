"""Utilitas pengolahan gambar umum (Pillow)."""

import io

from django.core.files.base import ContentFile
from PIL import Image, ImageOps


def open_normalized(file) -> Image.Image:
    """Buka gambar, koreksi orientasi EXIF, dan ubah ke RGB (metadata EXIF tidak ikut tersalin)."""
    file.seek(0)
    with Image.open(file) as src:
        img = ImageOps.exif_transpose(src)
        img.load()
    file.seek(0)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.getchannel("A"))
        return background
    return img.convert("RGB")


def fit_within(img: Image.Image, max_side: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return img


def to_jpeg_file(img: Image.Image, name: str = "image.jpg", quality: int = 85) -> ContentFile:
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    return ContentFile(buffer.getvalue(), name=name)


def resize_upload_to_jpeg(file, max_side: int = 1200, quality: int = 85) -> ContentFile:
    """Dipakai untuk foto makanan & logo: resize, hapus EXIF, simpan sebagai JPEG."""
    return to_jpeg_file(fit_within(open_normalized(file), max_side), quality=quality)
