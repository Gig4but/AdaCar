"""Viewing several cars in one drive (PLAN §4.6): cars marked in the trip window, in the order
with the least driving from the search's start point and back (routing/tour.py)."""

import logging
from urllib.parse import parse_qsl, urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from adacar.core import utcnow
from adacar.db.models import Car, table
from adacar.routing import deeplinks
from adacar.routing.commute import UNJUDGED
from adacar.routing.motis import MotisUnavailable
from adacar.routing.tour import MAX_STOPS, round_trip
from adacar.search.travel import origin_of
from adacar.web.pages import Svc, query_string, render

router = APIRouter()
log = logging.getLogger(__name__)


def visit_count(s: Session) -> int:
    return s.scalar(select(func.count(Car.id)).where(Car.visit_at.is_not(None))) or 0


def _point(car: Car) -> tuple[float, float] | None:
    """The point to drive to (a region's centre is none)."""
    loc = car.location
    if loc is None or loc.lat is None or loc.lon is None or loc.precision in UNJUDGED:
        return None
    return (loc.lat, loc.lon)


@router.post("/cars/{car_id}/visit", response_class=HTMLResponse)
def mark_visit(
    request: Request, svc: Svc, car_id: int, on: bool = True, tour: bool = False
) -> Response:
    """Marks the car for the viewing trip (`on`), or takes it off; from the trip itself
    (`tour`) the trip is shown again. Set, not toggled: a double click or a window opened
    before a change does not undo it."""
    with svc.sessions() as s:
        car = s.get(Car, car_id)
        if car is None:
            return Response(status_code=404)
        marked = on
        if (car.visit_at is not None) != on:
            # Not through the ORM: a mark does not change what the filter counts cache holds.
            s.execute(
                update(table(Car)).where(Car.id == car_id).values(visit_at=utcnow() if on else None)
            )
            s.commit()
        visits = visit_count(s)
    if tour:
        return visits_window(request, svc, oob=True)
    return render(
        request, "_visit_button.html", car_id=car_id, marked=marked, visits=visits, oob=True
    )


@router.get("/visits", response_class=HTMLResponse)
def visits_window(request: Request, svc: Svc, oob: bool = False) -> HTMLResponse:
    """The marked cars in driving order, with the minutes of each drive and the whole trip
    in Mapy.com or Google Maps."""
    f = svc.travel.filters(request.query_params)
    origin = origin_of(f)
    with svc.sessions() as s:
        cars = list(s.scalars(select(Car).where(Car.visit_at.is_not(None)).order_by(Car.visit_at)))
        found = [(c, _point(c)) for c in cars]
        placed = [(c, p) for c, p in found if p is not None]
        context = {
            "f": f,
            "origin": origin,
            "cars": cars,
            "unplaced": [c for c, p in found if p is None] if origin else [],
            "rest": [c for c, _ in placed[MAX_STOPS:]] if origin else [],
            "stops": [],
            "max_stops": MAX_STOPS,
            "visits": len(cars),
            "oob": oob,
            "qs": urlencode(
                [(k, v) for k, v in parse_qsl(query_string(request)) if k not in ("on", "tour")]
            ),
        }
        placed = placed[:MAX_STOPS]
        if origin is not None and placed:
            points = [p for _, p in placed]
            try:
                trip = round_trip(svc.commute, origin.point, points)
            except (httpx.HTTPError, MotisUnavailable, ValueError, KeyError):
                log.warning("The viewing trip by the map failed", exc_info=True)
                trip = round_trip(svc.commute, origin.point, points, use_map=False)
            ordered = [placed[i][0] for i in trip["order"]]
            path = [origin.point, *(points[i] for i in trip["order"]), origin.point]
            context |= {
                "stops": list(zip(ordered, trip["legs"], strict=False)),
                "back": trip["legs"][-1],
                "total": sum(trip["legs"]),
                "exact": trip["exact"],
                "links": {"mapy": deeplinks.mapy_tour(path), "google": deeplinks.google_tour(path)},
            }
        return render(request, "_tour.html", **context)
