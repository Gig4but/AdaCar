"""SQLite schema. Changes go through Alembic migrations in db/migrations/versions."""

from datetime import date, datetime
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
    """Minutes to a location (PLAN §4.6), computed as far as each mode's limit (by car 6 h,
    on foot 2 h: routing/commute.py MAX_MINUTES), so moving the travel-time slider only
    filters. Stored by key only (WITHOUT ROWID): one lookup per car."""

    __tablename__ = "travel_times"
    __table_args__ = {"sqlite_with_rowid": False}

    set_id: Mapped[int] = mapped_column(ForeignKey("travel_sets.id"), primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), primary_key=True)
    minutes: Mapped[int | None]  # None: farther than the mode's limit
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
    # Marked for a viewing: the cars of one drive in the best order (routing/tour.py).
    visit_at: Mapped[datetime | None] = mapped_column(index=True)
    # Warning signs (ai/enrich.py: rules and the AI), e.g. ["deposit", "price_low"].
    warnings: Mapped[list[str]] = mapped_column(default=list)
    # The checks' warnings (checks/service.py), merged into `warnings` by the rules pass; a
    # column of their own, so the checks and the AI never write over each other's.
    check_warnings: Mapped[list[str]] = mapped_column(default=list)
    ai_hash: Mapped[str | None] = mapped_column(String(64))  # content_hash the AI last read
    # The dealer behind the ad (checks/dealers.py), by its name; None for private sellers.
    dealer_id: Mapped[int | None] = mapped_column(
        ForeignKey("dealers.id", ondelete="SET NULL"), index=True
    )

    location: Mapped[Location | None] = relationship()
    ads: Mapped[list["CarAd"]] = relationship(back_populates="car", cascade="all, delete-orphan")
    images: Mapped[list["CarImage"]] = relationship(
        back_populates="car", cascade="all, delete-orphan", order_by="CarImage.position"
    )
    dealer: Mapped["Dealer | None"] = relationship(lazy="selectin")  # its warnings on the card

    @property
    def photos(self) -> list["CarImage"]:
        """The photos, the cover first: the car from the front, else from outside, as the AI
        saw them (ai/enrich.py); photos it has not looked at keep their place before the
        ones it saw as no car (a dealer's logo, an interior)."""
        return sorted(self.images, key=lambda i: COVER_RANK.get(i.kind or "", 2))

    @property
    def all_warnings(self) -> list[str]:
        """The car's warning signs and its dealer's ("dealer_insolvency")."""
        dealer = self.dealer.warnings if self.dealer else []
        return [*(self.warnings or []), *(f"dealer_{w}" for w in dealer)]


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


# Photo kinds the AI tells apart (ai/vision.py), best cover first; not looked at (or not
# downloaded, or added by the user): 2.
COVER_RANK = {"front": 0, "exterior": 1, "interior": 3, "detail": 3, "other": 3}


class CarImage(Base):
    """A photo of a car, by its URL on the source (shown, not downloaded), or a picture the
    user added (`url` "/uploads/<file>", kept in the data folder)."""

    __tablename__ = "car_images"
    __table_args__ = (UniqueConstraint("car_id", "url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    car_id: Mapped[int] = mapped_column(ForeignKey("cars.id", ondelete="CASCADE"), index=True)
    url: Mapped[str]
    position: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime]
    # What the AI saw (ai/vision.py: front | exterior | interior | detail | other), "failed"
    # when it could not be downloaded, "upload" for a user's picture.
    kind: Mapped[str | None] = mapped_column(String(10))
    text: Mapped[str | None]  # an added picture's text, as the AI read it

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


class VehicleCheck(Base):
    """What is known of a car by its VIN (checks/vehicle.py), kept when its ads are gone: the
    km the app saw in its ads, and the vehicle register's record."""

    __tablename__ = "vehicle_checks"

    vin: Mapped[str] = mapped_column(String(17), primary_key=True)
    km_seen: Mapped[list[dict[str, Any]]] = mapped_column(default=list)  # [{"on", "km"}]
    register: Mapped[dict[str, Any]] = mapped_column(default=dict)  # checks/register.facts
    # ok | not_found; None: not asked (no key, or not due yet)
    register_status: Mapped[str | None] = mapped_column(String(10))
    register_at: Mapped[datetime | None]


class Dealer(Base):
    """A dealer as the ads name it (one row per name key, checks/names.py), the company
    behind it and what the official registers and reviews say of it (checks/dealers.py)."""

    __tablename__ = "dealers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_key: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str]  # as an ad writes it
    ico: Mapped[str | None] = mapped_column(String(8), index=True)
    # How the IČO was found: ares | user; uncertain (candidates to pick from) | none
    # (no company of that name); "" not tried yet.
    match: Mapped[str] = mapped_column(String(10), default="")
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    resolved_at: Mapped[datetime | None]
    # From ARES, ISIR and the VAT list (checks/official.py):
    legal_name: Mapped[str | None]
    legal_form: Mapped[str | None] = mapped_column(String(4))
    founded: Mapped[date | None]
    ended: Mapped[date | None]
    address: Mapped[str | None]
    dic: Mapped[str | None] = mapped_column(String(14))
    insolvency: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    vat_unreliable: Mapped[str | None] = mapped_column(String(10))  # published since
    checked_at: Mapped[datetime | None]
    # Google Maps (with the user's key, checks/google.py):
    rating: Mapped[float | None]
    rating_count: Mapped[int | None]
    maps_url: Mapped[str | None]
    reviews: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    reviews_at: Mapped[datetime | None]
    # not_found | ended | insolvency | vat
    warnings: Mapped[list[str]] = mapped_column(default=list)


class LlmCache(Base):
    """Model answers by input, so an unchanged ad (or one seen on several portals) is read by
    the model only once. The key covers the model, the prompt version and the input."""

    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[int]
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime]
