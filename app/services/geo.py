import math


def parse_coords(lat: str, lng: str):
    """Optional coordinates: returns (lat, lng) or (None, None). Raises ValueError with a user-safe message."""
    lat, lng = (lat or "").strip(), (lng or "").strip()
    if not lat and not lng:
        return None, None
    if not lat or not lng:
        raise ValueError("Enter both latitude and longitude, or leave both blank.")
    try:
        a, b = float(lat), float(lng)
    except ValueError:
        raise ValueError("Latitude and longitude must be numbers.")
    if not (math.isfinite(a) and math.isfinite(b) and -90 <= a <= 90 and -180 <= b <= 180):
        raise ValueError("Latitude must be between -90 and 90, and longitude between -180 and 180.")
    return round(a, 6), round(b, 6)
