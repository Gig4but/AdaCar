"""Finds the same car offered on several sources (PLAN §4.4).

Dealers list each car on several portals, and private sellers often post on sauto.cz and
bazos.cz at once, each under its own ID, title and photos. A new ad is the same car as a
known one from another source when:
- both give the VIN and it is the same; or
- the make and year are the same, the place is the same district, the mileage differs by
  at most 2 % (or 2 000 km), the model names agree and the prices are within 15 %.
An ad without mileage is never merged by the second rule: too little to compare safely.
"""

import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from adacar.core import ParsedCar
from adacar.db.models import Car, CarAd, Location
from adacar.normalize.text import fold

KM_SHARE = 0.02
KM_ABSOLUTE = 2000
PRICE_SHARE = 0.15
SERIES = {"rada", "trida", "serie", "series", "class"}
VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")  # no I, O, Q


def valid_vin(vin: str | None) -> str | None:
    """The VIN in upper case, or None when it is not 17 valid characters."""
    vin = re.sub(r"\s", "", vin or "").upper()
    return vin if VIN.match(vin) else None


def model_key(model: str | None) -> str:
    """ "Octavia Combi" -> "octavia"; "Golf" -> "golf"; "Řada 3" -> "rada 3"."""
    words = re.findall(r"[a-z0-9]+", fold(model or ""))
    if len(words) > 1 and words[0] in SERIES:
        return " ".join(words[:2])
    return words[0] if words else ""


def models_agree(a: str, b: str) -> bool:
    if not a or not b or a == b:
        return True
    short, long = sorted((a, b), key=len)
    return short in SERIES and long.startswith(short + " ")


@dataclass
class Entry:
    """One known car, as far as dedup needs it."""

    car_id: int | None
    sources: set[str]
    vin: str | None
    make: str | None
    model: str
    year: int | None
    km: int | None
    price: int | None
    okres: str | None
    car: Car | None = None  # not yet flushed: no id

    @property
    def ref(self) -> int | Car:
        if self.car_id is None and self.car is not None and self.car.id is not None:
            self.car_id, self.car = self.car.id, None
        if self.car_id is not None:
            return self.car_id
        assert self.car is not None
        return self.car


def _close(a: int | None, b: int | None, share: float, absolute: int = 0) -> bool:
    if a is None or b is None:
        return True  # not known on one side: no evidence against
    return abs(a - b) <= max(absolute, share * max(a, b))


@dataclass
class DedupIndex:
    _by_vin: dict[str, list[Entry]] = field(default_factory=lambda: defaultdict(list))
    _blocks: dict[tuple[str, int], list[Entry]] = field(default_factory=lambda: defaultdict(list))
    # Sources are read in parallel: a store holds this while it looks up and adds a car.
    lock: threading.RLock = field(default_factory=threading.RLock)

    @classmethod
    def load(cls, session: Session, sessions=None) -> "DedupIndex":
        """Index of all active cars (read once per run). `sessions` is not needed: kept for
        the runner's call."""
        index = cls()
        ads = (
            select(CarAd.car_id, func.group_concat(CarAd.source_id).label("ids"))
            .where(CarAd.status == "active")
            .group_by(CarAd.car_id)
            .subquery()
        )
        rows = session.execute(
            select(
                Car.id, Car.vin, Car.make, Car.model, Car.year, Car.mileage_km, Car.price,
                Location.okres, ads.c.ids,
            )
            .join(ads, ads.c.car_id == Car.id)
            .outerjoin(Location, Location.id == Car.location_id)
            .where(Car.status == "active")
        )  # fmt: skip
        for r in rows:
            entry = Entry(r.id, set(r.ids.split(",")), valid_vin(r.vin), r.make,
                          model_key(r.model), r.year, r.mileage_km, r.price,
                          fold(r.okres) if r.okres else None)  # fmt: skip
            index.add(entry)
        return index

    @staticmethod
    def entry_for(parsed: ParsedCar, source_id: str) -> Entry:
        loc = parsed.location
        okres = fold(loc.okres) if loc and loc.okres else None
        return Entry(None, {source_id}, valid_vin(parsed.vin), parsed.make,
                     model_key(parsed.model), parsed.year, parsed.mileage_km, parsed.price,
                     okres)  # fmt: skip

    def add(self, entry: Entry) -> None:
        if entry.vin:
            self._by_vin[entry.vin].append(entry)
        if entry.make and entry.year:
            self._blocks[(entry.make, entry.year)].append(entry)

    def find(self, parsed: ParsedCar, source_id: str) -> Entry | None:
        new = self.entry_for(parsed, source_id)
        if new.vin:
            for e in self._by_vin.get(new.vin, ()):
                if source_id not in e.sources:
                    return e
        if not (new.make and new.year and new.km and new.okres):
            return None
        best, best_gap = None, None
        for e in self._blocks.get((new.make, new.year), ()):
            if source_id in e.sources or e.okres != new.okres or e.km is None:
                continue
            if new.vin and e.vin and new.vin != e.vin:
                continue
            if not models_agree(new.model, e.model):
                continue
            if not _close(new.km, e.km, KM_SHARE, KM_ABSOLUTE):
                continue
            if not _close(new.price, e.price, PRICE_SHARE):
                continue
            gap = abs(new.km - e.km)
            if best_gap is None or gap < best_gap:
                best, best_gap = e, gap
        return best
