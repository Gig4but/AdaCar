"""Viewing several cars in one drive (PLAN §4.6): from the start point to every marked car and
back, in the order with the least driving. Exact for up to `MAX_STOPS` cars (Held-Karp over
MOTIS car times between all the places), straight-line estimates without the map."""

import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise

from adacar.geo.places import distance_km
from adacar.routing.commute import ESTIMATE, MAX_MINUTES, CommuteService, street_seconds

Point = tuple[float, float]  # (lat, lon)

MAX_STOPS = 9  # Google Maps' route links take nine stops between start and end
# Seconds by car between two places, by map version: taking a car off the trip, or opening
# it again, asks MOTIS nothing (a row of the matrix costs it 1-3 s).
_known: dict[tuple[int, Point, Point], float | None] = {}
_known_lock = threading.Lock()
KNOWN_PAIRS = 5000


def estimated(a: Point, b: Point) -> float:
    """Minutes by car along the straight line (no limit: the order needs every pair)."""
    speed, start = ESTIMATE["car"]
    return start + distance_km(*a, *b) / speed * 60


def drive_matrix(
    motis, points: Sequence[Point], version: int = 0
) -> tuple[list[list[float]], bool]:
    """Minutes by car between every two points (MOTIS, one request per point whose times are
    not known yet, in parallel), and whether all are exact: a pair MOTIS does not route gets
    an estimate."""
    with _known_lock:
        known = {
            (a, b): _known[(version, a, b)]
            for a in points
            for b in points
            if (version, a, b) in _known
        }
    asked = [a for a in points if any((a, b) not in known for b in points if b != a)]
    if asked:

        def row(origin: Point) -> list[float | None]:
            return street_seconds(motis, origin, points, "car", MAX_MINUTES["car"] * 60)

        with ThreadPoolExecutor(min(8, len(asked)), thread_name_prefix="tour") as pool:
            for a, found in zip(asked, pool.map(row, asked), strict=True):
                known.update({(a, b): sec for b, sec in zip(points, found, strict=True)})
        with _known_lock:
            if len(_known) > KNOWN_PAIRS:
                _known.clear()
            _known.update({(version, a, b): sec for (a, b), sec in known.items()})
    exact = True
    minutes = []
    for a in points:
        out = []
        for b in points:
            sec = known.get((a, b))
            if a == b:
                out.append(0.0)
            elif sec is None:
                exact = False
                out.append(estimated(a, b))
            else:
                out.append(sec / 60)
        minutes.append(out)
    return minutes, exact


def estimate_matrix(points: Sequence[Point]) -> list[list[float]]:
    return [[0.0 if a == b else estimated(a, b) for b in points] for a in points]


def best_order(minutes: Sequence[Sequence[float]]) -> list[int]:
    """The stops 1..n in the order of the shortest round trip from point 0 and back
    (Held-Karp: every subset of stops with the stop it ends at)."""
    n = len(minutes) - 1
    if n <= 1:
        return list(range(1, n + 1))
    # best[mask][last]: the shortest way from 0 through the stops in `mask`, ending at `last`
    best: dict[tuple[int, int], tuple[float, int]] = {}
    for j in range(1, n + 1):
        best[(1 << (j - 1), j)] = (minutes[0][j], 0)
    for mask in range(1, 1 << n):  # a subset always comes before its supersets
        for last in range(1, n + 1):
            bit = 1 << (last - 1)
            if not mask & bit or mask == bit:
                continue
            rest = mask ^ bit
            best[(mask, last)] = min(
                (best[(rest, k)][0] + minutes[k][last], k)
                for k in range(1, n + 1)
                if rest & (1 << (k - 1))
            )
    full = (1 << n) - 1
    _, last = min((best[(full, j)][0] + minutes[j][0], j) for j in range(1, n + 1))
    order, mask = [], full
    while last:
        order.append(last)
        _, prev = best[(mask, last)]
        mask ^= 1 << (last - 1)
        last = prev
    return order[::-1]


def legs(minutes: Sequence[Sequence[float]], order: Sequence[int]) -> list[int]:
    """Minutes of each drive along the round trip: to the first stop, ..., back to 0."""
    return [round(minutes[a][b]) for a, b in pairwise([0, *order, 0])]


def round_trip(
    commute: CommuteService, origin: Point, stops: Sequence[Point], use_map: bool = True
) -> dict:
    """The stops' order (indices into `stops`), the minutes of each drive (the last one back
    to `origin`), and whether they are exact (MOTIS) or estimates (`use_map` False, the map
    not ready, or a place outside it)."""
    points = [origin, *stops]
    area = commute.data.area
    exact = use_map and commute.data.ready
    exact = exact and (area is None or all(area.contains(*p) for p in points))
    if exact and stops:
        with commute.server.use(commute.data.data) as motis:
            minutes, exact = drive_matrix(motis, points, commute.data.version)
    else:
        minutes, exact = estimate_matrix(points), False
    order = best_order(minutes)
    return {"order": [i - 1 for i in order], "legs": legs(minutes, order), "exact": exact}
