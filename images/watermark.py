"""Pembuatan preview ber-watermark & thumbnail (docs/02 §5).

Fungsi murni (tanpa akses DB/storage) agar mudah dipindah ke worker asinkron.
"""

import math

from django.core.files.base import ContentFile
from PIL import Image, ImageDraw, ImageFont

from core.imaging import fit_within, to_jpeg_file

PREVIEW_MAX_SIDE = 1200
THUMB_MAX_SIDE = 400


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def apply_watermark(img: Image.Image, text: str, opacity: float = 0.35, angle: float = 30) -> Image.Image:
    """Tempel teks berulang secara diagonal di seluruh gambar."""
    base = img.convert("RGBA")
    width, height = base.size
    font_size = max(14, int(min(width, height) / 14))
    font = _font(font_size)

    diagonal = int(math.hypot(width, height)) + font_size * 2
    layer = Image.new("RGBA", (diagonal, diagonal), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = right - left, bottom - top
    step_x, step_y = text_w + font_size * 2, text_h + font_size * 3
    alpha = int(255 * opacity)

    for row, y in enumerate(range(0, diagonal, step_y)):
        offset = (step_x // 2) * (row % 2)
        for x in range(-step_x + offset, diagonal, step_x):
            draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, alpha // 2))
            draw.text((x, y), text, font=font, fill=(255, 255, 255, alpha))

    layer = layer.rotate(angle, resample=Image.Resampling.BICUBIC)
    cx, cy = layer.width // 2, layer.height // 2
    layer = layer.crop((cx - width // 2, cy - height // 2, cx - width // 2 + width, cy - height // 2 + height))
    return Image.alpha_composite(base, layer).convert("RGB")


def generate_preview_and_thumbnail(img: Image.Image, text: str) -> tuple[ContentFile, ContentFile]:
    """img harus sudah dinormalisasi (RGB, orientasi EXIF benar, tanpa metadata)."""
    preview = apply_watermark(fit_within(img, PREVIEW_MAX_SIDE), text)
    thumb = fit_within(img, THUMB_MAX_SIDE)
    return to_jpeg_file(preview, "preview.jpg", quality=80), to_jpeg_file(thumb, "thumb.jpg", quality=80)
