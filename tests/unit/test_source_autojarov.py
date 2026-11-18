from pathlib import Path

import pytest

from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead
from adacar.scraper.sources import autojarov

SOURCES = Path(__file__).parents[1] / "fixtures" / "sources"
SOURCE = autojarov.SOURCE
DETAIL = "https://www.autojarov.cz/nabidka-vozu/ojete-audi-q2-35-tfsi-stronic-110-kw-automat-2896978.html"


def read(name: str) -> str:
    return (SOURCES / "autojarov" / name).read_text(encoding="utf-8")


@pytest.fixture
def places(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        municipalities(s)
        yield PlaceResolver(s)
    engine.dispose()


def test_listing_urls():
    assert SOURCE.listing_url_at(1) == "https://www.autojarov.cz/nabidka-vozu/typy_ojete/"
    assert SOURCE.listing_url_at(5) == "https://www.autojarov.cz/nabidka-vozu/typy_ojete/5/"


def test_listing_cards(places):
    page = SOURCE.parse_listing(read("listing.html"), 1, places)
    assert page.has_next and len(page.leads) == 12
    lead = page.leads[0]
    car = lead.card
    assert car is not None
    assert lead.external_id == "2880135" and lead.url == lead.fetch_url
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "skoda", "Octavia", 2024, 27221, 519900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.power_kw) == ("diesel", "manual", "combi", 85)
    assert car.seller_kind == "dealer" and car.seller_name == "Auto Jarov"
    assert car.place_text == "Praha 3 - Jarov"
    assert car.location is not None and car.location.municipality == "Praha"
    assert not car.images  # the site shows a "no photo" placeholder
    photos = [x.card.images[0] for x in page.leads if x.card and x.card.images]
    assert photos and all(p.startswith("https://www.autojarov.cz/uploads/") for p in photos)
    assert {x.card.place_text for x in page.leads if x.card} == {
        "Praha 3 - Jarov", "Praha 4 - Kunratice",
    }  # fmt: skip


def test_last_page(places):
    page = SOURCE.parse_listing(read("listing_last.html"), 62, places)
    assert not page.has_next and page.leads


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_2896978.html"), Lead("2896978", DETAIL), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "audi", "Q2", 2021, 52300, 434900,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.power_kw, car.engine_cc) == (
        "petrol", "automatic", 110, 1498,
    )  # fmt: skip
    assert (car.color, car.doors, car.seats) == ("černá", 5, 5)
    assert car.place_text == "Praha 4 - Kunratice"
    assert car.location is not None and car.location.precision == "address"
    assert "Tempomat" in car.equipment and len(car.images) > 10


def test_robots_allow_listing_and_detail():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_url_at(1)) and rules.allowed(SOURCE.listing_url_at(62))
    assert rules.allowed(DETAIL)
