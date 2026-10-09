import io
from datetime import datetime, timezone
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def build_pdf(manifest_id: str, rows: list) -> bytes:
    buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=A4); w, h = A4
    c.setFillColorRGB(.09, .16, .18); c.rect(0, h - 90, w, 90, fill=1, stroke=0)
    c.setFillColorRGB(.97, .72, 0); c.setFont("Helvetica-Bold", 24); c.drawString(40, h - 55, "WASTEMASTER")
    c.setFillColorRGB(1, 1, 1); c.setFont("Helvetica", 11); c.drawString(40, h - 75, "Construction Waste Manifest")
    c.setFillColorRGB(0, 0, 0); c.setFont("Helvetica-Bold", 14); c.drawString(40, h - 125, f"Manifest {manifest_id}")
    y = h - 160
    for label, value in rows:
        c.setFont("Helvetica-Bold", 10); c.drawString(40, y, label.upper())
        c.setFont("Helvetica", 11); c.drawString(200, y, str(value)[:80]); y -= 24
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(40, 40, f"Generated {datetime.now(timezone.utc):%d %b %Y %H:%M} UTC. CO2e figures are estimates.")
    c.showPage(); c.save()
    return buf.getvalue()
