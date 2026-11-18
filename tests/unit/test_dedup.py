from datetime import timedelta

import pytest
from sqlalchemy import select

from adacar.core import ParsedCar, ParsedLocation
from adacar.db.models import Car, CarAd, table
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.scraper.dedup import DedupIndex, model_key, valid_vin
from adacar.scraper.store import _CAR_FIELDS, _GAP_FIELDS, _PRICE_FIELDS, CarStore

LONG = (
    "Prodám Škodu Octavii Combi 2.0 TDI, první majitel, koupeno v ČR, servisní knížka. "
    "Pravidelný servis v autorizovaném servisu, poslední výměna oleje před 5 000 km, nové "
    "brzdy a rozvody. Tažné zařízení, výhřev sedadel, navigace, dvě sady kol. Nebourané, "
    "bez koroze, STK do roku 2027. Možnost prověření v autorizovaném servisu. "
)
VIN = "TMBJG7NE5J0123456"
OKRESY = {"533165": "Kolín", "554782": "Hlavní město Praha"}


def car(ext, km=120_000, price=250_000, place="533165", **kw):
    loc = ParsedLocation(key=f"obec:{place}", precision="municipality", municipality_code=place,
                         okres=OKRESY[place])  # fmt: skip
    fields = {"title": "Škoda Octavia Combi 2.0 TDI", "make": "skoda", "model": "Octavia Combi",
              "year": 2018} | kw  # fmt: skip
    return ParsedCar(ext, mileage_km=km, price=price, location=loc, **fields)


def indexed(*cars: tuple[ParsedCar, str]) -> tuple[DedupIndex, list]:
    index, entries = DedupIndex(), []
    for car_id, (parsed, source_id) in enumerate(cars, 1):
        entry = DedupIndex.entry_for(parsed, source_id)
        entry.car_id = car_id
        index.add(entry)
        entries.append(entry)
    return index, entries


def later(session, store_factory):
    """Windows clocks tick coarsely: let earlier sightings lie clearly before the next run."""
    for ad in session.scalars(select(CarAd)):
        ad.last_seen_at -= timedelta(minutes=1)
    session.commit()
    return store_factory()


@pytest.fixture
def session(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        yield s
    engine.dispose()


def test_every_parsed_field_has_a_column():
    columns = set(table(Car).columns.keys())
    assert set(_CAR_FIELDS) | {"is_offer"} <= columns
    assert set(_GAP_FIELDS) | set(_PRICE_FIELDS) <= set(_CAR_FIELDS)


def test_normalized_keys():
    assert valid_vin(" tmbjg7ne5j 0123456 ") == VIN
    assert valid_vin("TMBJG7NE5J012345") is None  # 16 characters
    assert valid_vin("TMBJG7NE5J012345O") is None  # no I, O or Q in a VIN
    assert valid_vin(None) is None
    assert model_key("Octavia Combi") == model_key("octavia") == "octavia"
    assert model_key("Mégane Grandtour") == "megane"
    assert model_key(None) == ""


def test_the_same_vin_on_another_source_is_the_same_car():
    index, (sauto,) = indexed((car("1", vin=VIN), "sauto"))
    # The VIN decides even when the ads tell the rest differently.
    other = car("2", km=180_000, price=150_000, place="554782", year=2017, vin=VIN.lower())
    assert index.find(other, "bazos") is sauto
    assert index.find(other, "sauto") is None  # same source: another car


def test_cars_without_a_vin_match_by_make_year_district_mileage_model_and_price():
    index, (sauto,) = indexed((car("1"), "sauto"))
    assert index.find(car("2", km=121_900, price=280_000), "bazos") is sauto
    assert index.find(car("2", model="Octavia"), "bazos") is sauto  # the first word agrees
    assert index.find(car("2", model=None, price=None), "bazos") is sauto  # nothing against
    assert index.find(car("2", vin=VIN), "bazos") is sauto  # a VIN on one side only
    district = car("2")
    district.location = ParsedLocation(key="okres:9533", precision="district", okres="Kolín")
    assert index.find(district, "bazos") is sauto

    assert index.find(car("2"), "sauto") is None  # same source: another car
    assert index.find(car("2", km=124_000), "bazos") is None  # over 2 % and 2 000 km
    assert index.find(car("2", km=None), "bazos") is None  # too little to compare
    assert index.find(car("2", price=300_000), "bazos") is None  # over 15 %
    assert index.find(car("2", year=2019), "bazos") is None
    assert index.find(car("2", make="volkswagen"), "bazos") is None
    assert index.find(car("2", model="Superb"), "bazos") is None
    assert index.find(car("2", place="554782"), "bazos") is None  # another district
    nowhere = car("2")
    nowhere.location = None
    assert index.find(nowhere, "bazos") is None


def test_mileage_may_differ_more_on_older_cars_and_the_closest_wins():
    index, (near, far) = indexed((car("1", km=300_000), "sauto"), (car("2", km=305_000), "sauto"))
    assert index.find(car("3", km=304_000), "bazos") is far
    assert index.find(car("3", km=294_500), "bazos") is near  # 2 % of 300 000 km
    index, (low,) = indexed((car("1", km=8_000), "sauto"))
    assert index.find(car("2", km=10_000), "bazos") is low  # 2 000 km
    assert index.find(car("2", km=10_100), "bazos") is None


def test_different_vins_are_different_cars():
    index, _ = indexed((car("1", vin=VIN), "sauto"))
    assert index.find(car("2", vin="TMBJG7NE5J0654321"), "bazos") is None


def test_store_merges_fills_gaps_and_expires_by_source(session):
    sauto = CarStore(session, "sauto")
    sauto.add_all([car("s1", price=None, description="Krátký popis.", power_kw=110)])
    bazos = CarStore(session, "bazos", dedup=sauto.dedup)
    bazos.add_all(
        [
            car("b1", km=121_000, price=240_000, description=LONG, vin=VIN, fuel="diesel"),
            car("b2", year=2012, model="Fabia"),
        ]
    )
    assert (bazos.stats.new, bazos.stats.merged) == (1, 1)

    merged = session.scalar(select(Car).where(Car.year == 2018))
    assert {ad.source_id: ad.price for ad in merged.ads} == {"sauto": None, "bazos": 240_000}
    assert (merged.vin, merged.fuel, merged.price) == (VIN, "diesel", 240_000)  # from bazos
    assert merged.mileage_km == 120_000 and merged.power_kw == 110  # the primary's
    assert merged.description == LONG  # the primary's was too short

    # The primary source updates without wiping what the other one filled in.
    sauto = CarStore(session, "sauto")
    sauto.add_all([car("s1", price=None, description="Nový krátký popis.", power_kw=110)])
    session.refresh(merged)
    assert (merged.vin, merged.price) == (VIN, 240_000)
    assert merged.description == "Nový krátký popis."

    # Sauto no longer offers it, bazos still does: the car stays, bazos takes over.
    sauto = later(session, lambda: CarStore(session, "sauto"))
    sauto.commit()
    assert sauto.expire_missing() == 0
    session.refresh(merged)
    primary = {ad.source_id: (ad.is_primary, ad.status) for ad in merged.ads}
    assert merged.status == "active"
    assert primary == {"sauto": (False, "expired"), "bazos": (True, "active")}

    bazos = later(session, lambda: CarStore(session, "bazos"))
    bazos.touch("b2")
    assert bazos.expire_missing() == 1  # b1 is gone from every source now
    session.refresh(merged)
    assert merged.status == "expired"
    assert session.scalar(select(Car.status).where(Car.year == 2012)) == "active"
    assert session.scalar(select(CarAd.status).where(CarAd.external_id == "b2")) == "active"


def test_store_counts_each_car_once_per_run(session):
    CarStore(session, "bazos").add_all([car("a", description=LONG), car("b", model="Fabia")])
    store = CarStore(session, "bazos")
    store.touch("a")
    store.add(car("a", description=LONG))
    store.touch("b")
    store.add(car("b", model="Fabia", price=245_000))
    store.commit()
    assert (store.stats.unchanged, store.stats.updated) == (1, 1)


def test_ads_selling_no_car_are_kept_but_not_offered(session):
    CarStore(session, "bazos").add_all([car("a"), car("b", extra={"not_offer": True})])
    offers = dict(session.execute(select(CarAd.external_id, Car.is_offer).join(Car)).all())
    assert offers == {"a": True, "b": False}
