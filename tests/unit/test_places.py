from pathlib import Path

import pytest

from adacar.config import Profile
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.geo.scope import build_query
from adacar.scraper.sources.bazos import location as bazos_location

SOURCES = Path(__file__).parents[1] / "fixtures" / "sources"


@pytest.fixture
def places(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        municipalities(s)
        yield PlaceResolver(s)
    engine.dispose()


@pytest.mark.parametrize(
    ("text", "key", "precision"),
    [
        ("Praha", "obec:554782", "municipality"),
        ("Praha-Krč", "part:554782:krc", "part"),
        ("Praha 4", "part:554782:praha 4", "part"),
        ("Praha 10 - Strašnice", "part:554782:strasnice", "part"),
        ("Brno - Líšeň", "part:582786:lisen", "part"),
        ("Kolín IV", "part:533165:kolin iv", "part"),
        ("Příbram VI-Březové Hory", None, None),  # not in the test data
        ("Okres Kolín", "okres:9533", "district"),
        ("Praha-východ", "okres:9538", "district"),  # a district, not a part of Praha
        ("Brandýs nad Labem-Stará Boleslav", "obec:538094", "municipality"),
        ("Kraj Vysočina", "kraj:kraj vysocina", "region"),
        ("Středočeský", "kraj:stredocesky kraj", "region"),
    ],
)
def test_resolve_place_names(places, text, key, precision):
    loc = places.resolve(text)
    assert (loc.key, loc.precision) == (key, precision) if key else loc is None


def test_resolve_details(places):
    assert places.resolve("Celá ČR") is None and places.resolve("Velká Británie") is None
    praha = places.resolve("Praha")
    assert praha.kraj == "Hlavní město Praha" and (praha.lat, praha.lon) == (50.08, 14.43)
    # Same name twice: the postal code decides, else the region, else the bigger one.
    assert places.resolve("Lhota", postal_code="763 23").municipality_code == "555657"
    assert places.resolve("Lhota", region="Zlínský kraj").municipality_code == "555657"
    assert places.resolve("Lhota").municipality_code == "549240"
    assert places.resolve("Lhota", district="Zlín").municipality_code == "555657"
    assert places.resolve(None, postal_code="30100").municipality == "Plzeň"
    street = places.resolve("Plzeň", street="Sedláčkova 245/24", postal_code="30100")
    assert street.key == "addr:554791:sedlackova:245/24" and street.precision == "address"
    assert street.address_text == "Sedláčkova 245/24, Plzeň" and street.lat is None
    assert places.resolve("Praha-Krč").address_text == "Praha-Krč"
    assert places.resolve("Praha 4").address_text == "Praha 4"
    region = places.resolve("Kraj Vysočina")
    assert region.lat == pytest.approx(49.40) and region.municipality_code is None


def test_home_and_crawl_scope(places):
    assert places.find_home("Nádražní 5, 280 02 Kolín").name == "Kolín"
    assert places.find_home("Kolín").name == "Kolín"
    assert places.find_home("nowhere") is None
    car = Profile(home_address="Kolín", max_minutes=45, travel_mode="car")
    near = build_query(places, car).kraje
    assert near[0] == "Středočeský kraj" and "Pardubický kraj" in near
    assert "Jihomoravský kraj" not in near
    walk = Profile(home_address="Kolín", max_minutes=30, travel_mode="walk")
    assert build_query(places, walk).kraje == ["Středočeský kraj"]
    assert len(build_query(places, Profile()).kraje) == 14  # home unknown: everywhere
    for m in places.municipalities:
        m.kraj = None
    assert len(build_query(places, car).kraje) == 14


def test_classified_places_by_psc_and_town(places):
    assert bazos_location(places, "Lhota", "763 23").municipality_code == "555657"
    assert bazos_location(places, "Lhota", "273 01").municipality_code == "549240"
    assert bazos_location(places, "Praha 4", None).key == "part:554782:praha 4"
    assert bazos_location(places, "Nowhere", "28002").municipality == "Kolín"
    assert bazos_location(places, None, "999 99") is None
