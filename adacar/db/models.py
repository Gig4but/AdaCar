"""SQLite schema. Changes go through Alembic migrations in db/migrations/versions."""

from datetime import datetime
from typing import Any, cast

from sqlalchemy import JSON, ForeignKey, Index, String, Table, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from adacar.core import CarStatus, Precision, RunStatus, SellerKind


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON, list[dict[str, Any]]: JSON}


def table(model: type[Base]) -> Table:
    """A model's table, for statements that bypass the ORM (bulk updates, fast inserts)."""
    return cast(Table, model.__table__)


class Location(Base):
    """A place shared by all cars at the same spot, so travel times are computed once."""

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # "ruian:<address code>", "addr:<obec>:<street>:<number>", "part:<obec>:<part>",
    # "obec:<code>", "okres:<codes>", "kraj:<name>"
    key: Mapped[str] = mapped_column(String(200), unique=True)
    lat: Mapped[float | None]
    lon: Mapped[float | None]
    # address | street | part | municipality | district | region | unknown
    precision: Mapped[Precision] = mapped_column(String(20), default="unknown")
    address_text: Mapped[str | None]
    municipality: Mapped[str | None]
    municipality_code: Mapped[str | None] = mapped_column(String(12))
    okres: Mapped[str | None]
    kraj: Mapped[str | None]


class TravelSet(Base):
    """Travel times from one start point, leaving at one time, by one mode (or "any": the
    fastest); the times refer to it by a small number, which keeps the filter fast."""

    __tablename__ = "travel_sets"
    __table_args__ = (UniqueConstraint("origin", "depart", "mode"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    origin: Mapped[str] = mapped_column(String(24))  # "lat,lon", 4 decimals
    depart: Mapped[str] = mapped_column(String(5))  # "07:00" on a weekday
    mode: Mapped[str] = mapped_column(String(8))  # car | transit | walk | bike | any


class TravelTime(Base):
    """Minutes to a location (PLAN §4.6), computed up to two hours ahead, so moving the
    travel-time slider only filters. Stored by key only (WITHOUT ROWID): one lookup per car."""

    __tablename__ = "travel_times"
    __table_args__ = {"sqlite_with_rowid": False}

    set_id: Mapped[int] = mapped_column(ForeignKey("travel_sets.id"), primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), primary_key=True)
    minutes: Mapped[int | None]  # None: not within two hours
    version: Mapped[int] = mapped_column(default=0)  # routing data version; 0 = an estimate


class Municipality(Base):
    """All Czech municipalities with a centre point (median of RÚIAN address points)."""

    __tablename__ = "municipalities"

    code: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str]
    name_normalized: Mapped[str] = mapped_column(index=True)  # lowercase, no diacritics
    okres_code: Mapped[str | None] = mapped_column(String(8), index=True)
    okres: Mapped[str | None]
    kraj: Mapped[str | None]
    lat: Mapped[float]
    lon: Mapped[float]
    address_count: Mapped[int] = mapped_column(default=0)
    postal_codes: Mapped[str] = mapped_column(default="")  # space-separated PSČ, e.g. "14000 14200"


class Car(Base):
    """A car for sale. The same car offered on several portals is one row with several ads
    (see scraper/dedup.py)."""

    __tablename__ = "cars"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    # Codes from adacar.normalize.vocab (`model`: the model's name as the source writes it)
    make: Mapped[str | None] = mapped_column(String(30), index=True)
    model: Mapped[str | None] = mapped_column(String(60))
    year: Mapped[int | None] = mapped_column(index=True)
    mileage_km: Mapped[int | None] = mapped_column(index=True)
    price: Mapped[int | None] = mapped_column(index=True)
    price_currency: Mapped[str] = mapped_column(String(3), default="CZK")
    vat_deductible: Mapped[bool | None]
    fuel: Mapped[str | None] = mapped_column(String(16))
    gearbox: Mapped[str | None] = mapped_column(String(16))
    body: Mapped[str | None] = mapped_column(String(16))
    drive: Mapped[str | None] = mapped_column(String(8))
    condition: Mapped[str | None] = mapped_column(String(10))
    power_kw: Mapped[int | None]
    engine_cc: Mapped[int | None]
    color: Mapped[str | None] = mapped_column(String(40))
    doors: Mapped[int | None]
    seats: Mapped[int | None]
    first_owner: Mapped[bool | None]
    service_book: Mapped[bool | None]
    crashed: Mapped[bool | None]
    origin_country: Mapped[str | None] = mapped_column(String(40))
    vin: Mapped[str | None] = mapped_column(String(17), index=True)
    seller_kind: Mapped[SellerKind | None] = mapped_column(String(10))
    seller_name: Mapped[str | None]
    seller_ico: Mapped[str | None] = mapped_column(String(8), index=True)
    place_text: Mapped[str | None]
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"), index=True)
    equipment: Mapped[list[str]] = mapped_column(default=list)
    posted_at: Mapped[datetime | None] = mapped_column(index=True)
    expires_at: Mapped[datetime | None]
    contact: Mapped[dict[str, Any]] = mapped_column(default=dict)
    extra: Mapped[dict[str, Any]] = mapped_column(default=dict)  # source-specific leftovers

    status: Mapped[CarStatus] = mapped_column(String(10), default="active", index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    # Classifieds in car categories that sell no car (buyers, scrap dealers, parts:
    # normalize/cars.py). Kept, so they are not read again, but never listed.
    is_offer: Mapped[bool] = mapped_column(default=True, index=True)
    hidden_at: Mapped[datetime | None] = mapped_column(index=True)  # hidden from the list

    location: Mapped[Location | None] = relationship()
    ads: Mapped[list["CarAd"]] = relationship(back_populates="car", cascade="all, delete-orphan")
    images: Mapped[list["CarImage"]] = relationship(
        back_populates="car", cascade="all, delete-orphan", order_by="CarImage.position"
    )


class CarAd(Base):
    """One source's ad of a car. One car can be offered on several portals.

    The primary ad owns the car's fields; the others only fill fields it leaves empty.
    A car expires when none of its ads is offered any more.
    """

    __tablename__ = "car_ads"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    car_id: Mapped[int] = mapped_column(ForeignKey("cars.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None]
    is_primary: Mapped[bool] = mapped_column(default=True)
    status: Mapped[CarStatus] = mapped_column(String(10), default="active")
    price: Mapped[int | None]  # this ad's price (a car may cost differently elsewhere)
    content_hash: Mapped[str | None] = mapped_column(String(64))  # of this ad's version
    detail_at: Mapped[datetime | None]  # when the detail page was read; None = listing only
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]

    car: Mapped[Car] = relationship(back_populates="ads")


class CarImage(Base):
    """A photo of a car, by its URL on the source (shown, not downloaded)."""

    __tablename__ = "car_images"
    __table_args__ = (UniqueConstraint("car_id", "url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    car_id: Mapped[int] = mapped_column(ForeignKey("cars.id", ondelete="CASCADE"), index=True)
    url: Mapped[str]
    position: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime]

    car: Mapped[Car] = relationship(back_populates="images")


class SourceState(Base):
    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    etag: Mapped[str | None]
    last_modified: Mapped[str | None]
    last_run_at: Mapped[datetime | None]
    state: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_source_started", "source_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    status: Mapped[RunStatus] = mapped_column(String(12), default="running")
    new_count: Mapped[int] = mapped_column(default=0)
    updated_count: Mapped[int] = mapped_column(default=0)
    removed_count: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None]


class SavedSearch(Base):
    """Filters the user saved by name; new matches are counted and can be notified."""

    __tablename__ = "saved_searches"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    query: Mapped[str]  # the search page's query string
    notify: Mapped[bool] = mapped_column(default=True)
    seen_up_to: Mapped[int] = mapped_column(default=0)  # cars after this id are new to the user
    created_at: Mapped[datetime]
