from pathlib import Path

import pytest

from adacar.core import ScrapeQuery
from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import sixt

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "sixt"
SOURCE = sixt.SOURCE
AD = "https://www.sixt.cz/land-rover-discovery-sport-20-d"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def places(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        municipalities(s)
        yield PlaceResolver(s)
    engine.dispose()


def test_listing_cards(places):
    page = SOURCE.parse_listing(read("listing.html"), 1, places)
    assert not page.has_next and len(page.leads) == 106  # the whole offer on one page
    lead = page.leads[0]
    assert lead.external_id == "land-rover-discovery-sport-20-d"
    assert lead.url == lead.fetch_url == AD
    car = lead.card
    assert car is not None and car.title == "Land Rover Discovery Sport 2.0 d D165 (120 kW)"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "land-rover", "Discovery Sport", 2021, 152065, 349000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw, car.color) == (
        "diesel", "automatic", 120, "šedá metalíza",
    )  # fmt: skip
    assert car.vat_deductible is True and car.seller_name == "SIXT Autobazar"
    assert "pohon 4x4" in car.equipment
    assert car.images == [
        "https://www.sixt.cz/User_Files/photos/thumbs/t400-m600_14098285_1-7c7e9fe2.jpg"
    ]
    cards = {x.external_id: x.card for x in page.leads if x.card}
    assert cards["skoda-octavia-14-tsi-style"].fuel == "plugin_hybrid"  # "hybridní - benzin"
    assert cards["skoda-octavia-15-style"].fuel == "cng"
    assert (cards["citroen-c5-x"].make, cards["citroen-c5-x"].model) == ("citroen", "C5 X")
    [(region, url)] = SOURCE.listings(ScrapeQuery(kraje=[]))
    assert region is None and SOURCE.nationwide and SOURCE.kind == "leasing"
    assert url(1) == "https://www.sixt.cz/nabidka-ojetych-vozidel-autobazar"


def test_detail(places):
    card = SOURCE.parse_listing(read("listing.html"), 1, places).leads[0]
    car = SOURCE.parse_detail(read("detail_1.html"), card, places)
    assert car is not None and car.title == "Land Rover Discovery Sport 2.0 d D165 (120 kW)"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "land-rover", "Discovery Sport", 2021, 152065, 349000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw, car.engine_cc) == (
        "diesel", "automatic", "suv", 120, 1999,
    )  # fmt: skip
    assert (car.doors, car.seats, car.first_owner, car.service_book) == (5, 5, False, False)
    assert car.vin == "SALCA2BNXMH885628" and "KOMISNÍM PRODEJI" in car.description
    assert car.place_text == "Praha 10-Štěrboholy"
    assert car.location is not None and car.location.municipality == "Praha"
    assert len(car.equipment) == 71 and len(car.images) == 10
    assert car.images[0] == "https://www.sixt.cz/User_Files/photos/14098285_1-7c7e9fe2.jpg"


def test_detail_van(places):
    url = "https://www.sixt.cz/opel-combo-15-cdti-edition-plus-15"
    car = SOURCE.parse_detail(read("detail_2.html"), Lead("x", url), places)
    assert car is not None and (car.make, car.model, car.price) == ("opel", "Combo", 290000)
    assert (car.fuel, car.gearbox, car.body, car.power_kw) == ("diesel", "manual", "mpv", 75)
    assert (car.first_owner, car.vin) == (True, "W0VEFYHT2NJ870524")
    assert SOURCE.parse_detail("<html></html>", Lead("1", "u"), places) is None


def test_robots_allow_offer_and_cars():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(sixt.LISTING) and rules.allowed(AD)
