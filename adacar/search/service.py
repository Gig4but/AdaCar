"""Car search: full text (FTS5), filters on normalized fields, facet counts, sorting."""

import itertools
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Literal
from urllib.parse import urlencode

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    case,
    event,
    exists,
    func,
    literal,
    literal_column,
    select,
    table,
    text,
)
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session, selectinload

from adacar.config import TIME_OF_DAY
from adacar.core import utcnow
from adacar.db.models import Car, CarAd, CarImage, Dealer, Location, SourceState
from adacar.routing.commute import travel_table, within

# Column weights for bm25: title, make, model, place_text, description.
_BM25 = "bm25(cars_fts, 10.0, 5.0, 5.0, 2.0, 1.0)"
_TOKEN = re.compile(r"\w+", re.UNICODE)

Sort = Literal["relevance", "date", "price", "price_desc", "km", "year", "travel"]
SORTS = ("relevance", "date", "price", "price_desc", "km", "year", "travel")
TRAVEL_MODES = ("any", "car", "transit", "walk", "bike")
MAX_TRAVEL = 120  # minutes; the slider's end (routing/commute.py computes this far)
# Single-value columns the list can be narrowed by, each with counts.
VALUE_FIELDS = ("make", "fuel", "gearbox", "body", "drive", "seller_kind")
CHECKS = ("no_warnings", "verified_dealer", "with_vin")  # switches: "1" or missing
FACETS = (*VALUE_FIELDS, "kraj", "sources")
RANGES = {  # filter name -> (column, highest accepted value)
    "price_min": ("price", 100_000_000),
    "price_max": ("price", 100_000_000),
    "year_min": ("year", 2100),
    "year_max": ("year", 2100),
    "km_max": ("mileage_km", 2_000_000),
    "power_min": ("power_kw", 2000),
}
BATCHES = "batches"  # SourceState row: the last car id stored before each search
MAX_BATCHES = 50


def fts_query(user_text: str) -> str | None:
    """Turn free text into a safe FTS5 query: every word of a phrase must match, as a
    prefix; phrases separated by commas are alternatives.

    "octavia rs" -> '"octavia"* AND "rs"*', "octavia, superb" -> '("octavia"*) OR
    ("superb"*)'. Quotes neutralize FTS5 operators.
    """
    queries = [q for q in map(_phrase_query, user_text.split(",")) if q]
    if len(queries) <= 1:
        return queries[0] if queries else None
    return " OR ".join(f"({q})" for q in queries)


def _phrase_query(phrase: str) -> str | None:
    tokens = [t.lower() for t in _TOKEN.findall(phrase)]
    return " AND ".join(f'"{t}"*' for t in tokens) if tokens else None


@dataclass
class Filters:
    q: str = ""
    make: list[str] = field(default_factory=list)
    fuel: list[str] = field(default_factory=list)
    gearbox: list[str] = field(default_factory=list)
    body: list[str] = field(default_factory=list)
    drive: list[str] = field(default_factory=list)
    seller_kind: list[str] = field(default_factory=list)
    kraj: list[str] = field(default_factory=list)
    sources_off: list[str] = field(default_factory=list)  # sources the user switched off
    price_min: int | None = None  # CZK
    price_max: int | None = None
    year_min: int | None = None
    year_max: int | None = None
    km_max: int | None = None
    power_min: int | None = None  # kW
    with_price: bool = False  # hide cars without a price ("dohodou")
    with_photo: bool = True  # hide cars without a photo
    # The checks (PLAN §4.5): no warning sign on the car or its dealer; a dealer checked in
    # the registers without a warning; the ad gives a VIN.
    no_warnings: bool = False
    verified_dealer: bool = False
    with_vin: bool = False
    posted_days: int | None = None
    # Travel time (PLAN §4.6): from the start point `origin` ("lat,lon", see geo/home.py) by
    # `travel_mode` ("any" = the fastest), leaving at `depart` on a weekday.
    origin: str | None = None
    origin_label: str = ""
    travel_mode: str = "any"
    travel_max: int | None = None  # minutes; None = not filtered by travel time
    depart: str = "09:00"
    sort: Sort = "relevance"

    def is_filtered(self) -> bool:
        plain = replace(self, sort="relevance", origin_label="")
        if not self.origin:
            plain = replace(plain, travel_mode="any", travel_max=None, depart="09:00")
        return plain != Filters()

    @property
    def travel_modes(self) -> tuple[str, ...]:
        return TRAVEL_MODES[1:] if self.travel_mode == "any" else (self.travel_mode,)

    def new_first(self) -> bool:
        """The last search's finds are listed above the cars found before: in the order
        found, or by rank when there are words to rank by."""
        return self.sort == "relevance"


def filters_from(qp, defaults: dict[str, str] | None = None) -> Filters:
    """Filters from query or form fields (anything with get and getlist); unknown or
    malformed values are ignored. `defaults`: values of fields that are missing (the makes,
    price and travel time from the user's profile; a list as comma-separated values)."""
    defaults = defaults or {}

    def get(name: str, default: str = "") -> str:
        value = qp.get(name)
        return defaults.get(name, default) if value is None else value

    def values(name: str) -> list[str]:
        if qp.get(name) is None and name in defaults:
            return [v for v in defaults[name].split(",") if v]
        return [v for v in qp.getlist(name) if v]

    def number(name: str, maximum: int) -> int | None:
        value = get(name).replace(" ", "")
        if re.fullmatch(r"[0-9]{1,9}", value) and int(value) <= maximum:
            return int(value)
        return None

    sort = qp.get("sort", "relevance")
    origin = re.fullmatch(r"(-?\d{1,2}\.\d{1,6}),(-?\d{1,3}\.\d{1,6})", get("at"))
    mode = get("mode", "any")
    depart = get("depart", "09:00")
    travel_max = number("max_min", MAX_TRAVEL)
    # The form sends photo=0 always and photo=1 when ticked; none (a link) = the default.
    photo = qp.getlist("photo")
    top = {name: maximum for name, (_, maximum) in RANGES.items()}
    return Filters(
        q=get("q"),
        make=values("make"),
        fuel=values("fuel"),
        gearbox=values("gearbox"),
        body=values("body"),
        drive=values("drive"),
        seller_kind=values("seller_kind"),
        kraj=values("kraj"),
        price_min=number("price_min", top["price_min"]),
        price_max=number("price_max", top["price_max"]),
        year_min=number("year_min", top["year_min"]),
        year_max=number("year_max", top["year_max"]),
        km_max=number("km_max", top["km_max"]),
        power_min=number("power_min", top["power_min"]),
        sources_off=[v for v in qp.getlist("source_off") if v],
        with_price=qp.get("with_price") == "1",
        with_photo="1" in photo if photo else True,
        no_warnings=qp.get("no_warnings") == "1",
        verified_dealer=qp.get("verified_dealer") == "1",
        with_vin=qp.get("with_vin") == "1",
        posted_days=number("posted_days", 3650),
        origin=f"{float(origin[1]):.4f},{float(origin[2]):.4f}" if origin else None,
        origin_label=get("from")[:200] if origin else "",
        travel_mode=mode if mode in TRAVEL_MODES else "any",
        travel_max=travel_max or None,  # 0 = "bez omezení"
        depart=depart if re.fullmatch(TIME_OF_DAY, depart) else "09:00",
        sort=sort if sort in SORTS and (sort != "travel" or origin) else "relevance",
    )


def filters_to_query(f: Filters) -> str:
    pairs: list[tuple[str, object]] = [("q", f.q)]
    for name in (*VALUE_FIELDS, "kraj"):
        # Even none: an empty value keeps the profile's default (e.g. its makes) away.
        pairs += [(name, v) for v in getattr(f, name)] or [(name, "")]
    pairs += [("source_off", v) for v in f.sources_off]
    for name in (*RANGES, "posted_days"):
        pairs.append((name, getattr(f, name) or ""))
    if f.with_price:
        pairs.append(("with_price", 1))
    if not f.with_photo:
        pairs.append(("photo", 0))
    pairs += [(name, 1) for name in CHECKS if getattr(f, name)]
    if f.origin:
        pairs += [("from", f.origin_label), ("at", f.origin), ("mode", f.travel_mode),
                  ("max_min", f.travel_max or 0), ("depart", f.depart)]  # fmt: skip
    return urlencode(pairs)


@dataclass
class SearchResult:
    total: int
    cars: list[Car]
    took_ms: float
    facets: dict[str, list[tuple[str, int]]] = field(default_factory=dict)


def _from_sources_on(off: list[str]) -> ColumnElement[bool]:
    """The car is offered by at least one source that is not switched off."""
    return exists(
        select(literal(1)).where(
            CarAd.car_id == Car.id,
            CarAd.status == "active",
            CarAd.source_id.not_in(off),
        )
    )


def _conditions(f: Filters, skip: frozenset[str] = frozenset()) -> list[ColumnElement[bool]]:
    """WHERE conditions for the filters; `skip` leaves facet dimensions out."""
    conds: list[ColumnElement[bool]] = [
        Car.status == "active",
        Car.is_offer.is_(True),
        Car.hidden_at.is_(None),
    ]
    for name in VALUE_FIELDS:
        chosen = getattr(f, name)
        if chosen and name not in skip:
            conds.append(getattr(Car, name).in_(chosen))
    if f.kraj and "kraj" not in skip:
        conds.append(Location.kraj.in_(f.kraj))
    if f.sources_off and "sources" not in skip:
        conds.append(_from_sources_on(f.sources_off))
    for name, (column, _) in RANGES.items():
        value = getattr(f, name)
        if value is not None:
            col = getattr(Car, column)
            conds.append(col <= value if name.endswith("_max") else col >= value)
    if f.with_price:
        conds.append(Car.price.is_not(None))
    if f.with_photo:
        conds.append(exists(select(literal(1)).where(CarImage.car_id == Car.id)))
    if f.posted_days:
        conds.append(Car.posted_at >= utcnow() - timedelta(days=f.posted_days))
    if f.no_warnings:
        conds.append(func.json_array_length(Car.warnings) == 0)
        warned = select(literal(1)).where(
            Dealer.id == Car.dealer_id, func.json_array_length(Dealer.warnings) > 0
        )
        conds.append(~exists(warned))
    if f.verified_dealer:
        checked = select(literal(1)).where(
            Dealer.id == Car.dealer_id,
            Dealer.checked_at.is_not(None),
            func.json_array_length(Dealer.warnings) == 0,
        )
        conds.append(exists(checked))
    if f.with_vin:  # 17 characters: not the "0" some sites write for none
        conds.append(func.length(Car.vin) == 17)
    return conds


def _base(f: Filters, columns, skip: frozenset[str] = frozenset(), travel=None):
    """`travel`: the travel-time table to join (for sorting by it); made here when the
    travel time filters."""
    stmt: Select = select(*columns).select_from(Car).outerjoin(Location)
    if f.origin and (f.travel_max or travel is not None):
        travel = travel if travel is not None else travel_table(f.origin, f.depart, f.travel_mode)
        stmt = stmt.outerjoin(travel, travel.c.location_id == Car.location_id)
        if f.travel_max:
            stmt = stmt.where(within(travel, Car.location_id, f.travel_max))
    hits = None
    if match := fts_query(f.q):
        hits = (
            select(literal_column("rowid").label("car_id"), literal_column(_BM25).label("rank"))
            .select_from(table("cars_fts"))
            .where(text("cars_fts MATCH :match").bindparams(match=match))
            .subquery()
        )
        stmt = stmt.join(hits, hits.c.car_id == Car.id)
    return stmt.where(and_(*_conditions(f, skip))), hits


# Per-database cache of every active car's facet values. Rebuilt only when the cars table
# changes (after a search), so a facet request needs just the matching ids from SQL plus one
# Python pass.
_facet_cache: dict[tuple[int, str], tuple[tuple, dict[int, tuple]]] = {}
# Commits of this process that changed cars. updated_at alone misses a change made within
# one tick of the clock (~16 ms on Windows); it still tells changes made by another process.
_car_commits = itertools.count(1)
_last_car_commit = 0


@event.listens_for(Session, "after_flush")
def _cars_flushed(session: Session, _context) -> None:
    changed = (*session.new, *session.dirty, *session.deleted)
    if any(isinstance(o, (Car, CarAd)) for o in changed):
        session.info["cars_changed"] = True


@event.listens_for(Session, "do_orm_execute")
def _cars_written(state) -> None:
    if (state.is_update or state.is_delete or state.is_insert) and state.bind_mapper in (
        Car.__mapper__,
        CarAd.__mapper__,
    ):
        state.session.info["cars_changed"] = True


@event.listens_for(Session, "after_commit")
def _cars_committed(session: Session) -> None:
    global _last_car_commit
    if session.info.pop("cars_changed", False):
        _last_car_commit = next(_car_commits)


@event.listens_for(Session, "after_rollback")
def _cars_rolled_back(session: Session) -> None:
    session.info.pop("cars_changed", None)


@event.listens_for(Session, "after_begin")
def _transaction_began(session: Session, _transaction, _connection) -> None:
    # What this transaction reads includes every car commit counted so far (maybe more).
    session.info["car_commits_seen"] = _last_car_commit


def _facet_index(session: Session) -> dict[int, tuple]:
    totals = session.execute(
        select(func.count(Car.id), func.total(Car.id), func.max(Car.updated_at)).where(
            Car.status == "active"
        )
    ).one()
    version = (session.info.get("car_commits_seen"), *totals)
    bind = session.get_bind()
    key = (id(bind), str(bind.engine.url))
    cached = _facet_cache.get(key)
    if cached and cached[0] == version:
        return cached[1]
    columns = [getattr(Car, n) for n in VALUE_FIELDS] + [Location.kraj]
    rows = session.execute(
        select(Car.id, *columns).outerjoin(Location).where(Car.status == "active")
    )
    sources: dict[int, set[str]] = {}
    for car_id, source_id in session.execute(
        select(CarAd.car_id, CarAd.source_id)
        .join(Car)
        .where(Car.status == "active", CarAd.status == "active")
    ):
        sources.setdefault(car_id, set()).add(source_id)
    index = {row[0]: (*row[1:], frozenset(sources.get(row[0], ()))) for row in rows}
    _facet_cache[key] = (version, index)
    return index


def facet_counts(session: Session, f: Filters) -> dict[str, list[tuple[str, int]]]:
    """Counts per facet value. Each facet ignores its own selection, so choosing one make
    still shows how many cars the other makes have."""
    index = _facet_index(session)
    stmt, _ = _base(f, [Car.id], skip=frozenset(FACETS))
    ids = session.scalars(stmt)

    checks = []  # (position, test) for each facet with an active selection
    for pos, name in enumerate(FACETS):
        if name == "sources":
            if f.sources_off:
                checks.append((pos, lambda v, off=frozenset(f.sources_off): not v <= off))
        elif chosen := frozenset(getattr(f, name)):
            checks.append((pos, lambda v, w=chosen: v in w))

    counts: list[dict[str, int]] = [{} for _ in FACETS]
    for car_id in ids:
        values = index.get(car_id)
        if values is None:
            continue
        failing = -1
        for pos, test in checks:
            if not test(values[pos]):
                if failing >= 0:
                    break  # fails two selections: counts nowhere
                failing = pos
        else:
            positions = range(len(FACETS)) if failing < 0 else (failing,)
            for pos in positions:
                bucket = counts[pos]
                value = values[pos]
                for item in value if isinstance(value, frozenset) else (value,):
                    if item is not None:
                        bucket[item] = bucket.get(item, 0) + 1
    return {
        name: sorted(c.items(), key=lambda kv: -kv[1])
        for name, c in zip(FACETS, counts, strict=True)
    }


def batch_starts(session: Session) -> list[int]:
    row = session.get(SourceState, BATCHES)
    return list((row.state or {}).get("starts", [])) if row else []


def start_batch(session: Session, last_id: int | None = None) -> None:
    """Cars stored from now on are listed above the ones before, in the order found.
    `last_id`: the last car id kept (after a clear, ids continue from it)."""
    cleared = last_id is not None
    if last_id is None:
        last_id = session.scalar(select(func.max(Car.id))) or 0
    row = session.get(SourceState, BATCHES) or SourceState(source_id=BATCHES)
    starts = [b for b in batch_starts(session) if b < last_id] + [last_id]
    row.state = {"starts": starts[-MAX_BATCHES:], "cleared": cleared}
    session.add(row)
    session.commit()


def new_after(session: Session) -> int | None:
    """Cars stored after this id are new: the last search found them. None when that search
    found everything (the first one, or one that started over)."""
    row = session.get(SourceState, BATCHES)
    state = (row.state or {}) if row else {}
    starts = state.get("starts") or [0]
    return None if state.get("cleared") or not starts[-1] else starts[-1]


def _batch_order(session: Session) -> tuple:
    """The last search's finds first, then those of the search before, …"""
    starts = batch_starts(session)
    batch = case(*((Car.id > b, i) for i, b in reversed(list(enumerate(starts, 1)))), else_=0)
    return (batch.desc(),) if starts else ()


def search_cars(
    session: Session,
    q: str | Filters = "",
    limit: int = 30,
    offset: int = 0,
    facets: bool = False,
    up_to_id: int | None = None,
) -> SearchResult:
    """`up_to_id`: only cars stored up to this one (the rest is shown by `cars_found_since`)."""
    started = time.perf_counter()
    f = q if isinstance(q, Filters) else Filters(q=q)
    known = [Car.id <= up_to_id] if up_to_id is not None else []

    count_stmt, _ = _base(f, [func.count()])
    total = session.scalar(count_stmt.where(*known)) or 0

    stmt = _sorted(session, f).where(*known).options(*_card_loads())
    cars = list(session.scalars(stmt.limit(limit).offset(offset)))
    result = SearchResult(total, cars, 0.0)
    if facets:
        result.facets = facet_counts(session, f)
    result.took_ms = (time.perf_counter() - started) * 1000
    return result


def _card_loads() -> tuple:
    """What a car card shows besides the car: its ads, place and photos."""
    return (selectinload(Car.ads), selectinload(Car.location), selectinload(Car.images))


def _sorted(session: Session, f: Filters) -> Select:
    """The matching cars in the order the list shows them."""
    newest = (Car.posted_at.desc().nulls_last(), Car.id.desc())
    if f.sort == "travel" and f.origin:
        travel = travel_table(f.origin, f.depart, f.travel_mode)
        stmt, _ = _base(f, [Car], travel=travel)
        return stmt.order_by(travel.c.minutes.asc().nulls_last(), *newest)
    stmt, hits = _base(f, [Car])
    by = {
        "price": Car.price.asc().nulls_last(),
        "price_desc": Car.price.desc().nulls_last(),
        "km": Car.mileage_km.asc().nulls_last(),
        "year": Car.year.desc().nulls_last(),
    }
    if f.sort in by:
        return stmt.order_by(by[f.sort], *newest)
    if f.sort == "relevance" and hits is not None:
        return stmt.order_by(*_batch_order(session), hits.c.rank.nulls_last(), Car.id.desc())
    if f.sort == "relevance":
        return stmt.order_by(*_batch_order(session), Car.id)
    return stmt.order_by(*newest)


def all_cars(session: Session, f: Filters, columns, batch: int = 1000) -> Iterator[Row]:
    """Every matching car in list order as plain rows of `columns` (Car and Location columns),
    read in batches."""
    stmt = _sorted(session, f).with_only_columns(*columns)
    yield from session.execute(stmt.execution_options(yield_per=batch))


def in_list_order(
    session: Session, f: Filters, columns, where: list[ColumnElement[bool]], limit: int
) -> list[Row]:
    """`columns` of the matching cars' ads that meet `where`, in list order."""
    stmt = (
        _sorted(session, f)
        .with_only_columns(*columns)
        .join(CarAd, CarAd.car_id == Car.id)
        .where(*where)
        .limit(limit)
    )
    return list(session.execute(stmt))


def cars_found_since(
    session: Session, f: Filters, since: int, up_to_id: int, limit: int, skip=()
) -> tuple[list[Car], int]:
    """(Matching cars stored after `since` up to `up_to_id` in the order found, without the
    `skip` ids; how many cars match in all.) Lets the list add a running search's finds."""
    count_stmt, _ = _base(f, [func.count()])
    total = session.scalar(count_stmt.where(Car.id <= up_to_id)) or 0
    stmt, _ = _base(f, [Car])
    stmt = (
        stmt.where(Car.id > since, Car.id <= up_to_id, Car.id.not_in(skip))
        .order_by(Car.id)
        .options(*_card_loads())
        .limit(limit)
    )
    return list(session.scalars(stmt)), total


def still_matching(session: Session, f: Filters, ids: list[int]) -> set[int]:
    """Those of the cars that still match the filters (the others expired, were merged or
    hidden)."""
    stmt, _ = _base(f, [Car.id])
    return set(session.scalars(stmt.where(Car.id.in_(ids))))


def count_between(session: Session, f: Filters, after: int, up_to_id: int) -> int:
    stmt, _ = _base(f, [func.count()])
    stmt = stmt.where(Car.id > after, Car.id <= up_to_id)
    return session.scalar(stmt) or 0


def car_points(session: Session, f: Filters) -> list[tuple]:
    """(location id, lat, lon, precision, car count) for every location with matching cars."""
    columns = [Location.id, Location.lat, Location.lon, Location.precision, func.count(Car.id)]
    stmt, _ = _base(f, columns)
    stmt = stmt.where(Location.lat.is_not(None)).group_by(Location.id)
    return [tuple(row) for row in session.execute(stmt)]


def cars_at_location(
    session: Session, f: Filters, location_id: int, limit: int = 50
) -> tuple[list[Car], int]:
    count_stmt, _ = _base(f, [func.count()])
    total = session.scalar(count_stmt.where(Car.location_id == location_id)) or 0
    stmt, _ = _base(f, [Car])
    stmt = (
        stmt.where(Car.location_id == location_id)
        .order_by(Car.posted_at.desc().nulls_last(), Car.id.desc())
        .options(selectinload(Car.ads), selectinload(Car.location))
        .limit(limit)
    )
    return list(session.scalars(stmt)), total
