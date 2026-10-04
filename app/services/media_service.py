"""Shared limits for raster uploads and embedded JSON/XML images."""
MAX_IMAGE_BYTES = 10 * 1024 * 1024
SIGNATURES = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff",
              ".gif": b"GIF8", ".bmp": b"BM", ".webp": b"RIFF"}


def validate_image(content, extension):
    """Check size, extension and file signature; this is not a full image decoder."""
    extension = extension.lower()
    if extension not in SIGNATURES:
        raise ValueError("Підтримуються PNG, JPG, GIF, BMP та WebP")
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Зображення має бути не більше 10 МБ")
    if not content.startswith(SIGNATURES[extension]) or (extension == ".webp" and content[8:12] != b"WEBP"):
        raise ValueError("Вміст файлу не відповідає формату зображення")
    return extension
