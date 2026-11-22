"""Clear-sky solar model: sun position, direct irradiance and irradiance on a vertical window. No Home Assistant imports."""
from __future__ import annotations

import math

SOLAR_CONSTANT_W_M2 = 1353.0


def solar_elevation_azimuth(lat_deg: float, lon_deg: float, utc_hour: float, day_of_year: int) -> tuple[float, float]:
    """Sun elevation and azimuth in degrees (azimuth clockwise from north) for a UTC hour and day of year.

    Uses the declination and hour-angle formulas. Solar time is UTC plus longitude/15 hours; the equation of time is ignored.
    """
    if not -90.0 <= lat_deg <= 90.0:
        raise ValueError("latitude must be in [-90, 90]")
    if not -180.0 <= lon_deg <= 180.0:
        raise ValueError("longitude must be in [-180, 180]")
    if not 1 <= day_of_year <= 366:
        raise ValueError("day_of_year must be in [1, 366]")
    if not 0.0 <= utc_hour < 24.0:
        raise ValueError("utc_hour must be in [0, 24)")
    lat = math.radians(lat_deg)
    dec = math.radians(23.45 * math.sin(math.radians(360.0 / 365.0 * (284 + day_of_year))))
    hour_angle = math.radians(15.0 * ((utc_hour + lon_deg / 15.0) % 24.0 - 12.0))
    sin_el = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(hour_angle)
    elevation = math.asin(max(-1.0, min(1.0, sin_el)))
    azimuth = math.atan2(math.sin(hour_angle), math.cos(hour_angle) * math.sin(lat) - math.tan(dec) * math.cos(lat)) + math.pi
    return math.degrees(elevation), math.degrees(azimuth) % 360.0


def clear_sky_irradiance(elevation_deg: float) -> float:
    """Clear-sky irradiance in W/m2 from the Meinel air-mass model. Zero at or below the horizon, capped at 1353."""
    if elevation_deg <= 0.0:
        return 0.0
    sin_el = math.sin(math.radians(elevation_deg))
    air_mass = 1.0 / sin_el
    return min(SOLAR_CONSTANT_W_M2, SOLAR_CONSTANT_W_M2 * 0.7 ** (air_mass ** 0.678) * sin_el)


def window_irradiance(direct_w_m2: float, elevation_deg: float, azimuth_deg: float, window_azimuth_deg: float) -> float:
    """Irradiance on a vertical window: direct value times the cosine of the incidence angle, floored at 0."""
    if elevation_deg <= 0.0:
        return 0.0
    cos_incidence = math.cos(math.radians(elevation_deg)) * math.cos(math.radians(azimuth_deg - window_azimuth_deg))
    return direct_w_m2 * max(0.0, cos_incidence)


# Share of the clear-sky irradiance that reaches the window under each forecast condition.
CLOUD_FACTOR_CLEAR = 1.0
CLOUD_FACTOR_PARTLY_CLOUDY = 0.7
CLOUD_FACTOR_CLOUDY = 0.4
CLOUD_FACTOR_RAIN = 0.2
CONDITION_FACTORS: dict[str, float] = {
    "sunny": CLOUD_FACTOR_CLEAR,
    "clear-night": CLOUD_FACTOR_CLEAR,
    "windy": CLOUD_FACTOR_CLEAR,
    "partlycloudy": CLOUD_FACTOR_PARTLY_CLOUDY,
    "windy-variant": CLOUD_FACTOR_PARTLY_CLOUDY,
    "cloudy": CLOUD_FACTOR_CLOUDY,
    "fog": CLOUD_FACTOR_CLOUDY,
    "lightning": CLOUD_FACTOR_CLOUDY,
    "rainy": CLOUD_FACTOR_RAIN,
    "pouring": CLOUD_FACTOR_RAIN,
    "lightning-rainy": CLOUD_FACTOR_RAIN,
    "hail": CLOUD_FACTOR_RAIN,
    "snowy": CLOUD_FACTOR_RAIN,
    "snowy-rainy": CLOUD_FACTOR_RAIN,
}


def condition_factor(condition: str | None) -> float | None:
    """Clear-sky scale for a Home Assistant weather condition. None when the condition is missing or not in the table."""
    return None if condition is None else CONDITION_FACTORS.get(condition)
