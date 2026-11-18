"""What every source produces (a ParsedCar, independent of the source's own format), and the
values of the coded text columns of db/models.py."""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

# How exactly a location is known, from the house down to the region.
Precision = Literal["address", "street", "part", "municipality", "district", "region", "unknown"]
CarStatus = Literal["active", "expired"]  # also of a car's ad on one source
# "stopped": the app closed mid-run.
RunStatus = Literal["running", "ok", "partial", "failed", "stopped", "blocked", "skipped"]
SourceKind = Literal["portal", "classifieds"]  # groups of the Sources page
SellerKind = Literal["private", "dealer"]


def utcnow() -> datetime:
    """Naive UTC timestamp, the form SQLite stores."""
    return datetime.now(UTC).replace(tzinfo=None)


class Source(Protocol):
    """What every car source tells about itself; scraper/sources/__init__.py lists them all.
    When and how a source is read depends on its kind (scraper/runner.py)."""

    id: str
    label: str
    kind: SourceKind
    default_enabled: bool  # on until the user switches it off
    needs_consent: bool  # off until the user accepts a warning about the site's terms


@dataclass
class ParsedLocation:
    key: str  # stable dedup key, e.g. "ruian:18329012", "obec:537683", "okres:3210"
    precision: Precision
    address_text: str | None = None
    municipality: str | None = None
    municipality_code: str | None = None
    okres: str | None = None
    kraj: str | None = None
    lat: float | None = None
    lon: float | None = None


@dataclass
class ParsedCar:
    external_id: str
    title: str
    url: str | None = None
    description: str = ""
    # Codes from adacar.normalize.vocab; `make` is the make's code ("skoda"), `model` the
    # model's name as the source writes it ("Octavia").
    make: str | None = None
    model: str | None = None
    year: int | None = None  # of manufacture or first registration
    mileage_km: int | None = None
    price: int | None = None  # None: "dohodou" (by agreement) or not given
    price_currency: str = "CZK"
    vat_deductible: bool | None = None  # a dealer's price includes VAT the buyer can deduct
    fuel: str | None = None
    gearbox: str | None = None
    body: str | None = None
    drive: str | None = None
    condition: str | None = None
    power_kw: int | None = None
    engine_cc: int | None = None
    color: str | None = None
    doors: int | None = None
    seats: int | None = None
    first_owner: bool | None = None
    service_book: bool | None = None
    crashed: bool | None = None  # the seller says it was in an accident
    origin_country: str | None = None  # country the car was first sold in ("Česká republika")
    vin: str | None = None
    seller_kind: SellerKind | None = None
    seller_name: str | None = None
    seller_ico: str | None = None
    place_text: str | None = None
    location: ParsedLocation | None = None
    equipment: list[str] = field(default_factory=list)  # names as the source writes them
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    contact: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)  # photo URLs, the main one first

    def content_hash(self) -> str:
        data = asdict(self)
        data.pop("external_id")
        raw = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class ScrapeQuery:
    """What to look for, from the wizard profile (see geo/scope.py)."""

    kraje: list[str]
    home: str | None = None  # municipality name
    radius_km: float = 50
    makes: list[str] = field(default_factory=list)  # make codes; empty = every make
    max_price: int | None = None
