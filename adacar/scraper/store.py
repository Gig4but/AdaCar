"""Writes parsed cars into the database: insert new, merge duplicates, update changed, expire.

One car can be offered by several sources (see scraper/dedup.py), one ad each. The source
that created the car is its primary source and owns its fields; other sources only fill
fields the car lacks. A car expires when none of its ads is offered any more.
"""

import threading
from collections.abc import Collection, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta

from sqlalchemy import (
    ColumnElement,
    CursorResult,
    and_,
    case,
    exists,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.orm import Session, aliased, object_session

from adacar.core import ParsedCar, ParsedLocation, utcnow
from adacar.db.models import Car, CarAd, CarImage, Location
from adacar.scraper.dedup import DedupIndex, Entry

# Copied to the car's columns as they are; the rest goes to the ad's row, the location and
# the photos.
_CAR_FIELDS = [
    f.name for f in fields(ParsedCar) if f.name not in ("external_id", "url", "location", "images")
]
# Filled from a secondary ad when the car has no value. The price fields move together.
_GAP_FIELDS = [
    "make",
    "model",
    "year",
    "mileage_km",
    "vat_deductible",
    "fuel",
    "gearbox",
    "body",
    "drive",
    "condition",
    "power_kw",
    "engine_cc",
    "color",
    "doors",
    "seats",
    "first_owner",
    "service_book",
    "crashed",
    "origin_country",
    "vin",
    "seller_kind",
    "seller_name",
    "seller_ico",
    "equipment",
    "expires_at",
]
_PRICE_FIELDS = ["price", "price_currency"]
SHORT_DESCRIPTION = 200  # a secondary source's longer description replaces a shorter one
PRECISION_RANK = {
    "address": 0,
    "street": 1,
    "part": 2,
    "municipality": 3,
    "district": 4,
    "region": 5,
    "unknown": 6,
}


def car_values(parsed: ParsedCar) -> dict:
    """Column values for a car: the copied fields, and whether it is offered at all (a
    source marks an ad that sells no car with extra["not_offer"])."""
    values = {f: getattr(parsed, f) for f in _CAR_FIELDS}
    return values | {"is_offer": not parsed.extra.get("not_offer")}


def _empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


@dataclass
class StoreStats:
    new: int = 0
    merged: int = 0  # new for this source, but already known from another one
    updated: int = 0
    unchanged: int = 0
    expired: int = 0

    def counts(self) -> dict[str, int]:
        """What the search progress shows per source."""
        return {
            "new": self.new,
            "merged": self.merged,
            "updated": self.updated,
            "removed": self.expired,
        }


@dataclass
class _Known:
    car_id: int  # -1: added since the last commit (its row id is read back then)
    digest: str | None
    is_primary: bool
    detail_at: datetime | None

    @property
    def has_detail(self) -> bool:
        return self.detail_at is not None


class CarStore:
    """Upserts one source's cars. Built for large batches (a portal has ~100k ads)."""

    def __init__(
        self,
        session: Session,
        source_id: str,
        batch_size: int = 2000,
        dedup: DedupIndex | None = None,
        write_lock: AbstractContextManager | None = None,
    ) -> None:
        # Sources read in parallel write one at a time, in short transactions: SQLite allows
        # one writer, and a transaction left open while a page downloads would block the rest.
        self.lock = write_lock or threading.RLock()
        self.session = session
        self.source_id = source_id
        self.batch_size = batch_size
        self.stats = StoreStats()
        self.started_at = utcnow()
        rows = session.execute(
            select(
                CarAd.external_id,
                CarAd.car_id,
                CarAd.content_hash,
                CarAd.is_primary,
                CarAd.detail_at,
            ).where(CarAd.source_id == source_id)
        )
        self._known = {r[0]: _Known(r[1], r[2], r[3], r[4]) for r in rows}
        shared = select(CarAd.car_id).group_by(CarAd.car_id).having(func.count() > 1)
        self._shared_cars = set(session.scalars(shared))  # cars offered by several sources
        self.dedup = dedup if dedup is not None else DedupIndex.load(session)
        locations = session.execute(select(Location.key, Location.id, Location.precision))
        self._locations: dict[str, int] = {}
        self._precision: dict[int, str] = {}
        for key, loc_id, precision in locations:
            self._locations[key] = loc_id
            self._precision[loc_id] = precision
        self._pending = 0
        self._seen_external_ids: list[str] = []
        self._unchanged_ids: set[str] = set()
        self._new_entries: list[Entry] = []  # dedup entries whose car has no id yet
        self._added: list[str] = []  # external ids added since the last commit

    @contextmanager
    def writing(self) -> Iterator[None]:
        """Holds the write lock and commits at the end."""
        with self.lock:
            yield
            self.commit()

    # --- reading ----------------------------------------------------------------------------

    def knows(self, external_id: str) -> bool:
        return external_id in self._known

    def has_detail(self, external_id: str) -> bool:
        known = self._known.get(external_id)
        return bool(known and known.has_detail)

    def detail_older_than(self, external_id: str, when: datetime) -> bool:
        """The detail page was read before `when` (e.g. the source's last change): read it again."""
        known = self._known.get(external_id)
        return bool(known and known.detail_at and known.detail_at < when)

    def detail_read_at(self, external_id: str) -> datetime:
        """When the detail page was last read (datetime.min: never)."""
        known = self._known.get(external_id)
        return (known.detail_at if known else None) or datetime.min

    # --- writing ----------------------------------------------------------------------------

    def add_all(self, cars: Iterable[ParsedCar]) -> StoreStats:
        for car in cars:
            self.add(car)
        self.commit()
        return self.stats

    def touch(self, external_id: str) -> None:
        """The source still offers this car, unchanged as far as we know."""
        if external_id not in self._unchanged_ids:
            self._unchanged_ids.add(external_id)
            self.stats.unchanged += 1
        self._seen_external_ids.append(external_id)
        self._count()

    def add(self, parsed: ParsedCar, detail: bool = True) -> None:
        """`detail`: the record is complete (False: only what a listing card shows)."""
        now = utcnow()
        digest = parsed.content_hash()
        known = self._known.get(parsed.external_id)

        if known is None:
            with self.dedup.lock:
                match = self.dedup.find(parsed, self.source_id)
                ref = match.ref if match is not None else None
                if isinstance(ref, Car) and object_session(ref) is not self.session:
                    match = None  # another source's car, not saved yet: no id to join
                if match is not None:
                    self._attach(match, parsed, digest, detail, now)
                    self.stats.merged += 1
                else:
                    self._create(parsed, digest, detail, now)
                    self.stats.new += 1
            self._known[parsed.external_id] = _Known(
                -1, digest, match is None, now if detail else None
            )
            self._added.append(parsed.external_id)
        elif digest == known.digest:
            self.touch(parsed.external_id)
            if detail:  # read again (e.g. a newer `modified`) but unchanged
                known.detail_at = now
                self.session.execute(
                    update(CarAd)
                    .where(
                        CarAd.source_id == self.source_id,
                        CarAd.external_id == parsed.external_id,
                    )
                    .values(detail_at=now)
                )
            return
        elif known.car_id < 0:
            return  # a second, different copy in the same run (an API repeating an ad)
        else:
            self.stats.updated += 1
            if parsed.external_id in self._unchanged_ids:
                self._unchanged_ids.discard(parsed.external_id)
                self.stats.unchanged -= 1
            self._update(known, parsed, digest, detail, now)
            known.digest = digest
            if detail:
                known.detail_at = now
        self._count()

    def _count(self) -> None:
        self._pending += 1
        if self._pending >= self.batch_size:
            self.commit()

    def _source_row(self, parsed: ParsedCar, digest: str, detail: bool, now: datetime, primary):
        return CarAd(
            source_id=self.source_id,
            external_id=parsed.external_id,
            url=parsed.url,
            is_primary=primary,
            status="active",
            price=parsed.price,
            content_hash=digest,
            detail_at=now if detail else None,
            first_seen_at=now,
            last_seen_at=now,
        )

    def _create(self, parsed: ParsedCar, digest: str, detail: bool, now: datetime) -> None:
        car = Car(
            **car_values(parsed),
            location_id=self._location_id(parsed.location),
            content_hash=digest,
            status="active",
            first_seen_at=now,
            last_seen_at=now,
            updated_at=now,
        )
        car.ads.append(self._source_row(parsed, digest, detail, now, primary=True))
        self._add_images(car, parsed.images, set(), now)
        self.session.add(car)
        entry = DedupIndex.entry_for(parsed, self.source_id)
        if entry is not None:
            entry.car = car
            self.dedup.add(entry)
            self._new_entries.append(entry)

    def _attach(
        self, match: Entry, parsed: ParsedCar, digest: str, detail: bool, now: datetime
    ) -> None:
        ref = match.ref
        row = self._source_row(parsed, digest, detail, now, primary=False)
        car = ref if isinstance(ref, Car) else self.session.get_one(Car, ref)
        car.ads.append(row)
        match.sources.add(self.source_id)
        if car.id is not None:
            self._shared_cars.add(car.id)
        self._fill_gaps(car, parsed, now)

    def _fill_gaps(self, car: Car, parsed: ParsedCar, now: datetime) -> None:
        """A secondary source completes what the primary one left out."""
        changed = False
        for name in _GAP_FIELDS:
            new = getattr(parsed, name)
            if _empty(getattr(car, name)) and not _empty(new):
                setattr(car, name, new)
                changed = True
        if car.price is None and parsed.price is not None:
            for name in _PRICE_FIELDS:
                setattr(car, name, getattr(parsed, name))
            changed = True
        if len(car.description or "") < SHORT_DESCRIPTION and len(parsed.description) > len(
            car.description or ""
        ):
            car.description = parsed.description
            changed = True
        if parsed.location is not None:
            current = (
                self._precision.get(car.location_id, "unknown") if car.location_id else "unknown"
            )
            if PRECISION_RANK.get(parsed.location.precision, 6) < PRECISION_RANK.get(current, 6):
                car.location_id = self._location_id(parsed.location)
                changed = True
        if changed:
            car.updated_at = now
        if parsed.images:
            self._add_images(car, parsed.images, {i.url for i in car.images}, now)
        car.status = "active"
        car.last_seen_at = now

    def _update(
        self, known: _Known, parsed: ParsedCar, digest: str, detail: bool, now: datetime
    ) -> None:
        source_values = {
            "last_seen_at": now,
            "url": parsed.url,
            "status": "active",
            "price": parsed.price,
            "content_hash": digest,
        }
        if detail:
            source_values["detail_at"] = now
        self.session.execute(
            update(CarAd)
            .where(
                CarAd.source_id == self.source_id,
                CarAd.external_id == parsed.external_id,
            )
            .values(**source_values)
        )
        if not known.is_primary:
            car = self.session.get(Car, known.car_id)
            if car is not None:
                self._fill_gaps(car, parsed, now)
            return
        values = car_values(parsed)
        others = known.car_id in self._shared_cars
        if others:  # keep what other sources filled in
            values = {k: v for k, v in values.items() if not _empty(v)}
        location_id = self._location_id(parsed.location)
        if location_id is not None or not others:
            values["location_id"] = location_id
        values |= {
            "content_hash": digest,
            "status": "active",
            "last_seen_at": now,
            "updated_at": now,
        }
        self.session.execute(update(Car).where(Car.id == known.car_id).values(**values))
        if parsed.images:
            car = self.session.get(Car, known.car_id)
            if car is not None:
                self._add_images(car, parsed.images, {i.url for i in car.images}, now)

    @staticmethod
    def _add_images(car: Car, urls: list[str], have: set[str], now: datetime) -> None:
        """New pictures of an ad; ones already stored are kept with their text. A path
        without a host is a site's own "no photo" picture, or one the app could not load."""
        position = len(have)
        for url in dict.fromkeys(urls):
            if url not in have and url.startswith(("https://", "http://")):
                car.images.append(CarImage(url=url, position=position, created_at=now))
                position += 1

    def commit(self) -> None:
        if self._seen_external_ids:
            mark_seen(self.session, self.source_id, self._seen_external_ids)
            self._seen_external_ids.clear()
        self.session.flush()
        with self.dedup.lock:
            for entry in self._new_entries:
                _ = entry.ref  # swaps the flushed Car object for its id
        self._new_entries.clear()
        # A card stored now and its detail page read later in the run update one car.
        for start in range(0, len(self._added), 500):
            ids = self._added[start : start + 500]
            rows = select(CarAd.external_id, CarAd.car_id).where(
                CarAd.source_id == self.source_id, CarAd.external_id.in_(ids)
            )
            for external_id, car_id in self.session.execute(rows):
                self._known[external_id].car_id = car_id
        self._added.clear()
        self.session.commit()
        self._pending = 0

    def expire_missing(self, kraje: Collection[str] | None = None) -> int:
        """After a complete pass over a source: what it no longer offers is gone from it.

        `kraje`: the pass covered only these regions; cars placed in other regions stay.
        """
        return self._expire_seen_before(self.started_at, kraje)

    def expire_unseen(self, days: int) -> int:
        """For searches that show only the newest ads: unseen for `days` means gone."""
        return self._expire_seen_before(utcnow() - timedelta(days=days))

    def unseen_for(self, days: int, limit: int) -> list[tuple[str, str]]:
        """(external_id, url) of active ads unseen for `days`, the longest unseen first."""
        rows = self.session.execute(
            select(CarAd.external_id, CarAd.url)
            .where(
                CarAd.source_id == self.source_id,
                CarAd.status == "active",
                CarAd.last_seen_at < utcnow() - timedelta(days=days),
                CarAd.url.is_not(None),
            )
            .order_by(CarAd.last_seen_at)
            .limit(limit)
        )
        return [(external_id, url) for external_id, url in rows if url]

    def expire_gone(self, external_ids: Collection[str]) -> int:
        """Ads whose own page says they are gone (the listings were not read to the end)."""
        if not external_ids:
            return 0
        return self._expire_seen_before(utcnow(), external_ids=external_ids)

    def _expire_seen_before(
        self,
        cutoff: datetime,
        kraje: Collection[str] | None = None,
        external_ids: Collection[str] | None = None,
    ) -> int:
        self.commit()
        expired, moved = expire_ads(self.session, self.source_id, cutoff, kraje, external_ids)
        self.stats.expired += expired
        for external_id, is_primary in self.session.execute(
            select(CarAd.external_id, CarAd.is_primary).where(
                CarAd.car_id.in_(moved), CarAd.source_id == self.source_id
            )
        ):
            if external_id in self._known:
                self._known[external_id].is_primary = is_primary
        self.session.commit()
        return expired

    def _location_id(self, loc: ParsedLocation | None) -> int | None:
        if loc is None:
            return None
        if loc.key not in self._locations:
            # Another source read in parallel may have added this place since we loaded ours.
            found = (
                self.session.connection()
                .execute(select(Location.id, Location.precision).where(Location.key == loc.key))
                .first()
            )
            if found is not None:
                self._locations[loc.key], self._precision[found.id] = found.id, found.precision
                return found.id
            insert_row = insert(Location).values(asdict(loc))
            loc_id = self.session.connection().execute(insert_row).lastrowid
            self._locations[loc.key], self._precision[loc_id] = loc_id, loc.precision
        return self._locations[loc.key]

    def mostly_unseen(self, kraje: Collection[str] | None = None) -> bool:
        """Fewer than half of the cars the source offered before this pass were seen in it.

        Then the dump or the pages are rather broken than so many cars gone at once, so the
        caller expires nothing. A source with fewer than 20 cars may lose them all.
        `kraje`: the pass covered only these regions, as for `expire_missing`.
        """
        self.commit()
        offered = [
            CarAd.source_id == self.source_id,
            CarAd.first_seen_at < self.started_at,
            CarAd.status == "active",
        ]
        if kraje is not None:
            offered.append(_in_regions(kraje))
        last_seen = self.session.scalars(select(CarAd.last_seen_at).where(*offered)).all()
        unseen = sum(when < self.started_at for when in last_seen)
        return len(last_seen) >= 20 and unseen * 2 > len(last_seen)


def _in_regions(kraje: Collection[str]) -> ColumnElement[bool]:
    """Sources whose car is in one of `kraje` or has no region (a pass covering only those)."""
    elsewhere = (
        select(Car.id)
        .join(Location, Location.id == Car.location_id)
        .where(Location.kraj.is_not(None), Location.kraj.not_in(list(kraje)))
    )
    return CarAd.car_id.not_in(elsewhere)


def mark_seen(session: Session, source_id: str, external_ids: Collection[str]) -> None:
    """The source still offers these ads (also an ad that had expired): seen now."""
    now = utcnow()
    ids = list(external_ids)
    seen = select(CarAd.car_id).where(CarAd.source_id == source_id, CarAd.external_id.in_(ids))
    session.execute(
        update(Car)
        .where(Car.id.in_(seen))
        .values(
            last_seen_at=now,
            status="active",
            updated_at=case((Car.status == "active", Car.updated_at), else_=now),
        )
        .execution_options(synchronize_session=False)
    )
    session.execute(
        update(CarAd)
        .where(CarAd.source_id == source_id, CarAd.external_id.in_(ids))
        .values(last_seen_at=now, status="active")
        .execution_options(synchronize_session=False)
    )


def expire_ads(
    session: Session,
    source_id: str,
    cutoff: datetime,
    kraje: Collection[str] | None = None,
    external_ids: Collection[str] | None = None,
) -> tuple[int, list[int]]:
    """The source's ads last seen before `cutoff` (only in `kraje`, or only `external_ids`,
    when given) are no longer offered by it; a car no source offers any more expires.
    (Cars expired, ids of cars whose primary source moved to another.) Not committed."""
    stale = [
        CarAd.source_id == source_id,
        CarAd.last_seen_at < cutoff,
        CarAd.status == "active",
    ]
    if external_ids is not None:
        stale.append(CarAd.external_id.in_(list(external_ids)))
    if kraje is not None:
        stale.append(_in_regions(kraje))
    session.execute(update(CarAd).where(*stale).values(status="expired"))
    active_source = exists().where(CarAd.car_id == Car.id, CarAd.status == "active")
    result = session.execute(
        update(Car)
        .where(Car.status == "active", ~active_source)
        .values(status="expired", updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    assert isinstance(result, CursorResult)
    return result.rowcount or 0, _move_primary(session)


def _move_primary(session: Session) -> list[int]:
    """Cars whose primary source expired but another source still offers them: the other
    one becomes primary. Returns their ids."""
    other = aliased(CarAd)
    orphaned = select(CarAd.car_id).where(
        CarAd.is_primary,
        CarAd.status == "expired",
        exists().where(and_(other.car_id == CarAd.car_id, other.status == "active")),
    )
    car_ids = list(session.scalars(orphaned))
    if not car_ids:
        return []
    heirs = (
        select(func.min(CarAd.id))
        .where(CarAd.car_id.in_(car_ids), CarAd.status == "active")
        .group_by(CarAd.car_id)
    )
    session.execute(
        update(CarAd)
        .where(CarAd.car_id.in_(car_ids))
        .values(is_primary=CarAd.id.in_(heirs))
        .execution_options(synchronize_session=False)
    )
    return car_ids
