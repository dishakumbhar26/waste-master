import io, qrcode


def qr_png(url: str) -> bytes:
    buf = io.BytesIO()
    qrcode.make(url).save(buf, format="PNG")
    return buf.getvalue()
