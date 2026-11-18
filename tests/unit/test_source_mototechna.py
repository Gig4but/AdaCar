from pathlib import Path

import pytest

from adacar.db.session import make_engine, make_sessionmaker, migrate
from adacar.geo.places import PlaceResolver
from adacar.net.robots import RobotsRules
from adacar.scraper.crawl import Lead, merge_card
from adacar.scraper.sources import mototechna

SOURCES = Path(__file__).parents[1] / "fixtures" / "sources"
SOURCE = mototechna.SOURCE
DETAIL = "https://www.mototechna.cz/detail/bmw/x5/34856518"


def read(name: str) -> str:
    return (SOURCES / "mototechna" / name).read_text(encoding="utf-8")


@pytest.fixture
def places(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        municipalities(s)
        yield PlaceResolver(s)
    engine.dispose()


def test_listing_urls():
    assert SOURCE.listing_url_at(1) == "https://www.mototechna.cz/prodej-vozu"
    assert SOURCE.listing_url_at(4) == "https://www.mototechna.cz/prodej-vozu?page=4"
    assert not SOURCE.newest_first


def test_listing_cards(places):
    page = SOURCE.parse_listing(read("listing_p2.html"), 2, places)
    assert page.has_next and len(page.leads) == 20
    lead = page.leads[0]
    car = lead.card
    assert car is not None
    assert lead.url == lead.fetch_url == "https://www.mototechna.cz/detail/lexus/lm/24694672"
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "lexus", "LM", 2025, 10180, 2200000,
    )  # fmt: skip
    assert (car.gearbox, car.body, car.drive) == ("automatic", "mpv", "4x4")
    assert car.seller_kind == "dealer" and car.seller_name == "Mototechna"
    assert car.place_text == "Mototechna Praha - Hloubětín"
    assert car.location is not None and car.location.municipality == "Praha"
    at_aaa = next(x.card for x in page.leads if x.external_id == "26765539")
    assert at_aaa is not None and at_aaa.place_text == "Praha 8 - Dolní Chabry"


def test_detail(places):
    car = SOURCE.parse_detail(read("detail_34856518.html"), Lead("34856518", DETAIL), places)
    assert car is not None
    assert (car.make, car.model, car.year, car.mileage_km, car.price) == (
        "bmw", "X5", 2025, 24548, 2300000,
    )  # fmt: skip
    assert (car.fuel, car.gearbox, car.body, car.drive) == (
        "plugin_hybrid", "automatic", "suv", "4x4",
    )  # fmt: skip
    assert (car.power_kw, car.engine_cc, car.seats, car.color) == (360, 2998, 5, "bílá")
    assert len(car.equipment) > 20 and len(car.images) > 10


def test_card_fills_the_place_the_detail_lacks(places):
    lead = SOURCE.parse_listing(read("listing_p2.html"), 2, places).leads[0]
    car = SOURCE.parse_detail(read("detail_34856518.html"), lead, places)
    assert car is not None
    assert merge_card(car, lead.card).place_text == "Mototechna Praha - Hloubětín"


def test_robots_allow_listing_and_detail():
    rules = RobotsRules.parse(read("robots.txt"), "adacar")
    assert rules.allowed(SOURCE.listing_url_at(2))
    assert rules.allowed(DETAIL)
