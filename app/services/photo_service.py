"""Validate, normalise and re-encode an uploaded photo. Re-encoding as JPEG strips EXIF (incl. GPS) and anything non-image."""
import io
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard
MAX_UPLOAD = 8_000_000
MAX_SIDE = 1600


class PhotoError(ValueError):
    pass


def process_photo(data: bytes) -> bytes:
    if not data:
        raise PhotoError("The photo file is empty.")
    if len(data) > MAX_UPLOAD:
        raise PhotoError("The photo is too large (8 MB maximum).")
    try:
        probe = Image.open(io.BytesIO(data)); probe.verify()
        im = Image.open(io.BytesIO(data))
        if im.format not in ("JPEG", "PNG", "WEBP"):
            raise PhotoError("Please upload a JPEG, PNG or WebP image.")
        im = ImageOps.exif_transpose(im).convert("RGB"); im.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO(); im.save(out, "JPEG", quality=82)
        return out.getvalue()
    except PhotoError:
        raise
    except Exception:
        raise PhotoError("That file is not a valid JPEG, PNG or WebP image.")
