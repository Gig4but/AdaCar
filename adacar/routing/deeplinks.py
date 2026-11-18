"""The same route opened in Mapy.com, IDOS or Google Maps (links only, no API or key)."""

from datetime import datetime
from urllib.parse import urlencode

Point = tuple[float, float]  # (lat, lon)

GOOGLE_MODES = {"car": "driving", "transit": "transit", "walk": "walking", "bike": "bicycling"}
# Mapy.com has no public transport in its route links (developer.mapy.com, "Mapy.com URL").
MAPY_MODES = {"car": "car_fast", "walk": "foot_fast", "bike": "bike_road"}


def google(origin: Point, destination: Point, mode: str) -> str:
    return "https://www.google.com/maps/dir/?" + urlencode(
        {
            "api": 1,
            "origin": f"{origin[0]},{origin[1]}",
            "destination": f"{destination[0]},{destination[1]}",
            "travelmode": GOOGLE_MODES[mode],
        }
    )


def mapy(origin: Point, destination: Point, mode: str) -> str | None:
    if mode not in MAPY_MODES:
        return None
    return "https://mapy.com/fnc/v1/route?" + urlencode(
        {
            "start": f"{origin[1]},{origin[0]}",  # lon,lat
            "end": f"{destination[1]},{destination[0]}",
            "routeType": MAPY_MODES[mode],
        }
    )


def google_tour(points: list[Point]) -> str:
    """A drive from the first point through the others to the last."""
    return "https://www.google.com/maps/dir/?" + urlencode(
        {
            "api": 1,
            "origin": f"{points[0][0]},{points[0][1]}",
            "destination": f"{points[-1][0]},{points[-1][1]}",
            "waypoints": "|".join(f"{lat},{lon}" for lat, lon in points[1:-1]),
            "travelmode": "driving",
        }
    )


def mapy_tour(points: list[Point]) -> str:
    """The same in Mapy.com ("waypoints": up to 15, lon,lat separated by ";")."""
    return "https://mapy.com/fnc/v1/route?" + urlencode(
        {
            "start": f"{points[0][1]},{points[0][0]}",
            "end": f"{points[-1][1]},{points[-1][0]}",
            "waypoints": ";".join(f"{lon},{lat}" for lat, lon in points[1:-1]),
            "routeType": MAPY_MODES["car"],
        }
    )


def idos(origin_name: str, destination_name: str, when: datetime, arrive: bool = False) -> str:
    """Public transport connections by place names (IDOS does not take coordinates)."""
    params = {
        "f": origin_name,
        "t": destination_name,
        "date": f"{when.day}.{when.month}.{when.year}",
        "time": f"{when.hour}:{when.minute:02d}",
    }
    if arrive:
        params["arr"] = "true"
    return "https://idos.cz/vlakyautobusymhdvse/spojeni/vysledky/?" + urlencode(params)
