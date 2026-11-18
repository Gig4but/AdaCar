"""What to look for: the regions reachable from home within the user's travel-time limit,
plus the home town, a search radius, the makes and the highest price from the profile.

Portals are read region by region, so a user in Kolín does not wait for every listing page of
Ostrava. Straight-line distance at the best speed of the travel mode keeps this an upper
bound; the exact travel-time filter is applied later on the cars themselves.
"""

import math

from adacar.config import Profile
from adacar.core import ScrapeQuery
from adacar.geo.places import KRAJE, REACH_KM_PER_MINUTE, PlaceResolver

MIN_RADIUS_KM = 15


def _speed(mode: str) -> float:
    return max(REACH_KM_PER_MINUTE.values()) if mode == "any" else REACH_KM_PER_MINUTE[mode]


def radius_km(profile: Profile) -> float:
    if not profile.max_minutes:  # no limit: every region, nearest first
        return math.inf
    return max(MIN_RADIUS_KM, profile.max_minutes * _speed(profile.travel_mode))


def build_query(places: PlaceResolver, profile: Profile | None) -> ScrapeQuery:
    """Every region, and no home, when the home address is unknown; every region also when
    the home's region is unknown."""
    makes = list(profile.makes) if profile else []
    max_price = profile.max_price if profile else None
    home = places.find_home(profile.home_address) if profile and profile.home_address else None
    if home is None or profile is None:
        return ScrapeQuery(kraje=list(KRAJE), makes=makes, max_price=max_price)
    km = radius_km(profile)
    kraje = places.kraje_within(home.lat, home.lon, km)
    if home.kraj and home.kraj not in kraje:
        kraje.insert(0, home.kraj)
    return ScrapeQuery(
        kraje=kraje or list(KRAJE), home=home.name, radius_km=km, makes=makes, max_price=max_price
    )
