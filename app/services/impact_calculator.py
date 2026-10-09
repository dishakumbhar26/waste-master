"""All CO2e maths lives here. Factors come from the EnvironmentalFactor table.
A factor with an empty `source` is an unverified placeholder; add verified values via the table."""
from sqlmodel import select
from app.models import EnvironmentalFactor

PLACEHOLDERS = {"Concrete": 0.02, "Metal": 1.5, "Wood": 0.8, "Brick": 0.05, "Glass": 0.3, "Plastic": 1.0, "Other": 0.1, "Mixed Construction Waste": 0.1}


def ensure_factors(s):
    if not s.exec(select(EnvironmentalFactor.id)).first():
        for m, f in PLACEHOLDERS.items():
            s.add(EnvironmentalFactor(material=m, factor=f, source=""))
        s.commit()


def estimate(s, by):
    """by: {material: tonnes}. Returns (tCO2e estimate, all_factors_verified)."""
    fx = {f.material: f for f in s.exec(select(EnvironmentalFactor)).all()}
    total, verified = 0.0, bool(by)
    for m, t in by.items():
        f = fx.get(m)
        total += t * (f.factor if f else 0.1)
        verified = verified and bool(f and f.source)
    return round(total, 1), verified
